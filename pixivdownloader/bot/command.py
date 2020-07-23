from io import BytesIO
from itertools import chain, islice
from multiprocessing import Manager, JoinableQueue, Process
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse
from zipfile import ZipFile
import logging
import re

from PIL import Image, UnidentifiedImageError
from pixiv.downloader import PixivDownloader, PixivDownloaderError
from telegram import Bot, Update, InputMediaPhoto, ParseMode
from telegram.ext import run_async, MessageHandler, Filters
from telegram.error import TelegramError

from pixivdownloader.bot.bot import main_bot
from pixivdownloader.bot.settings import PIXIV_USERNAME, PIXIV_PASSWORD, URL, DOWNLOAD_TO


class Command:
    post_link = 'https://www.pixiv.net/en/artworks/{}'
    _refresh_token_path = Path('pixiv-credentials').absolute()

    def __init__(self):
        self.client = PixivDownloader()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.out_dir = Path(DOWNLOAD_TO)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.login()

        main_bot.add_command(name='start', func=self.start)
        main_bot.add_command(MessageHandler, func=self.all_from_user,
                             filters=Filters.text & Filters.regex(re.compile('https://www.pixiv.net(/\\w+)?/users/\\d+', flags=re.IGNORECASE)))
        main_bot.add_command(MessageHandler, func=self.downloader,
                             filters=(Filters.text
                                      & (Filters.regex(re.compile('https://www.pixiv.net(/\\w+)?/artworks/\\d+', flags=re.IGNORECASE))
                                         | Filters.regex(re.compile('^[\\s\\d]+$', flags=re.IGNORECASE)))
                                     )
                            )

    def start(self, bot: Bot, update: Update):
        update.message.reply_markdown("""Hey,

I am here to help you download posts from [Pixiv](https://pixiv.net/).
Just send me a link or the id of the post and I'll give you the images / videos.
""")

    @property
    def refresh_token(self):
        if not self._refresh_token_path.is_file():
            return None
        return self._refresh_token_path.read_text().strip()

    @refresh_token.setter
    def refresh_token(self, value):
        if not value:
            return
        if not self._refresh_token_path.is_file():
            self._refresh_token_path.touch()
        self._refresh_token_path.write_text(value)

    def login(self):
        logged_in = False
        if self.refresh_token:
            try:
                self.client.login(None, None, self.refresh_token)
                logged_in = True
                self.logger.info('Logged in with refresh_token')
            except:
                self.logger.info('Login with refresh_token failed')

        if not logged_in:
            token = self.client.login(PIXIV_USERNAME, PIXIV_PASSWORD)
            self.logger.info('Logged in with username/password')
            self.refresh_token = token.get('response', {}).get('refresh_token')

    def _chunks(self, iterable, size=10):
        """https://stackoverflow.com/a/24527424
        """
        iterator = iter(iterable)
        for first in iterator:
            yield chain([first], islice(iterator, size - 1))

    def _file_to_bytes(self, path: Path) -> BytesIO:
        buffer = BytesIO()
        buffer.write(path.read_bytes())
        buffer.seek(0)
        return buffer

    def _resize_if_necessary(self, path: Path) -> Image:
        MAX = 2000
        try:
            image = Image.open(path)
            if MAX < image.height >= image.width:
                print(path)
                return image.resize((int(image.width * (MAX / image.height)), MAX), Image.ANTIALIAS)
            elif MAX < image.width >= image.height:
                print(path)
                return image.resize((MAX, int(image.height * (MAX / image.width))), Image.ANTIALIAS)
        except UnidentifiedImageError:
            pass

    def _with_url_possible(self, path: Path) -> bool:
        size = path.stat().st_size / 1000 / 1000  # In MB
        extension = path.suffix.lower().lstrip('.')
        return URL and (
            (extension in ['png', 'jpg', 'jpeg', 'webp'] and size <= 5)
            or
            (extension in ['mp4'] and size <= 20)
        )

    def _file_to_upload(self, path: Path) -> str or BytesIO:
        small = path.with_name(path.stem + '-small' + path.suffix)
        if not small.is_file():
            image = self._resize_if_necessary(path)
            if image:
                image.save(small)
                path = small
        else:
            path = small

        if self._with_url_possible(path):
            return URL.rstrip('/') + '/' + str(path.relative_to(self.out_dir))
        else:
            return self._file_to_bytes(path)

    def downloader(self, bot: Bot, update: Update):
        message = update.effective_message
        text = message.text
        tried_relogin = False

        if not text:
            message.reply_text('No URL or ID supplied').result()
            return

        ids = re.findall('(\\d+)', text.replace('\n', ' '))
        total = len(ids)
        for index, id in enumerate(ids, 1):
            downloadin_msg = message.reply_markdown(f'Downloading `{id}`', reply_to_message_id=message.message_id)
            downloadin_msg = downloadin_msg.result()
            paths = None

            try:
                id, paths = self._simple_download(id)
            except PixivDownloaderError:
                if not tried_relogin:
                    try:
                        self.login()
                        id, paths = self._simple_download(id)
                    except:
                        pass
                    finally:
                        tried_relogin = True
            if paths:
                self._send_to_user(id, paths, bot, update, prefix=f'{index}/{total} ' if total > 1 else '')
            else:
                message.reply_markdown(f'Post `{id}` not found', reply_to_message_id=message.message_id)

            downloadin_msg.delete()

    def _send_to_user(self, id, paths, bot, update, prefix=''):
        chat_id = update.effective_chat.id
        message = update.effective_message
        default_kwargs = {
            'timeout': 120,
            'reply_to_message_id': message.message_id,
            'parse_mode': ParseMode.MARKDOWN,
        }

        for index, chunk in enumerate(self._chunks(paths, 10)):
            works = list(chunk)
            kwargs = default_kwargs.copy()
            kwargs['caption'] = f'{prefix}[{id}]({self.post_link})'.format(id)

            if len(works) == 1:
                work = works[0]
                if work.suffix == '.mp4':
                    bot.send_video(chat_id, self._file_to_upload(work), **kwargs)
                else:
                    bot.send_photo(chat_id, self._file_to_upload(work), **kwargs)
            else:
                media_group = [InputMediaPhoto(self._file_to_upload(path)) for path in works]
                media_group[0].caption = kwargs['caption']
                media_group[0].parse_mode = kwargs['parse_mode']

                bot.send_media_group(chat_id, media_group, **kwargs)

    def _send_as_zip(self, paths, filename, update, additional_files=None, caption=None):
        additional_files = additional_files or {}

        bytes = BytesIO()
        with ZipFile(bytes, mode='w') as zipfile:
            for path in paths:
                zipfile.write(path, path.name)
            for additional_file, content in additional_files.items():
                zipfile.writestr(additional_file, content)
        bytes.seek(0)
        update.effective_message.reply_document(bytes, filename=filename, caption=caption, timeout=120)

    def _simple_download(self, id_or_post):
        if isinstance(id_or_post, dict):
            post = id_or_post
            post_id = id_or_post['id']
        else:
            post = None
            post_id = id_or_post

        path = self.out_dir / str(post_id)
        path.mkdir(parents=True, exist_ok=True)
        downloads = list(path.glob('*.*'))
        if downloads:
            return post_id, list(downloads)

        self.logger.info(f'Start downloading post {post_id}')
        if post:
            downloader = self.client.download(post, path)
        else:
            downloader = self.client.download_by_id(post_id, path)

        resultset = []
        try:
            for index, path in enumerate(downloader):
                new_path = path.parent / f'p{post_id}-{index}{path.suffix}'
                path.rename(new_path)
                resultset.append(new_path)
        except Exception as e:
            self.logger.exception(e)
            return post_id, []
        return post_id, resultset

    def all_from_user(self, bot: Bot, update: Update):
        tried_relogin = False
        url = update.effective_message.text
        zip_it = 'zip' in url

        ids = re.findall('(\\d+)', url.replace('\n', ' '))
        for id in ids:
            try:
                self._download_all_of_user(bot, update, id, zip_it=zip_it)
            except Exception as e:
                self.logger.exception(e)
                if not isinstance(e, TelegramError) and not tried_relogin:
                    try:
                        self.login()
                        self.logger.info('Trying relogging')
                        self._download_all_of_user(bot, update, id, zip_it=zip_it)
                        continue
                    except:
                        pass
                    finally:
                        tried_relogin = True

                update.effective_message.reply_text(f'Could not finish sending {id}\'s works for unknown reason')

    class Sender:
        def __init__(self, parent, zip_it, user_id, total, update, bot):
            self.parent = parent
            self.zip_it = zip_it
            self.user_id = user_id
            self.total = total
            self.update = update
            self.bot = bot

            manager = Manager()
            self.current_size = manager.Value('i', 0)
            self.xth_zip = manager.Value('i', 1)
            self.next_zip = manager.dict()
            self.lock = manager.Lock()

            self.logger = logging.getLogger(self.__class__.__name__)

        def _send_as_zip(self, id, paths, index):
            size = sum(map(lambda path: path.stat().st_size, paths))
            size = size / 1000 / 1000
            is_last = index == self.total

            if self.current_size.value + size >= 50 or is_last:
                send_last_lonely = False
                if is_last and self.current_size.value + size <= 50:
                    self.next_zip[id] = paths
                elif is_last:
                    send_last_lonely = True

                try:
                    self.logger.info(f'Sending zip with {self.next_zip.keys()}')
                    post = index if index == self.total else index - 1

                    self.parent._send_as_zip(
                        paths=chain(*self.next_zip.values()),
                        filename=f'{self.user_id} - {self.xth_zip.value}.zip',
                        update=self.update,
                        additional_files={'posts.txt': '\n'.join(map(str, self.next_zip.keys())) + '\n'},
                        caption=f'{post}/{self.total}'
                    )
                    if send_last_lonely:
                        self.logger.info(f'Sending zip with {self.next_zip.keys()}')
                        self.parent._send_as_zip(
                            paths=paths,
                            filename=f'{self.user_id} - {self.xth_zip.value + 1}.zip',
                            update=self.update,
                            additional_files={'posts.txt': str(id) + '\n'},
                            caption=f'{self.total}/{self.total}'
                        )
                except Exception as e:
                    self.update.effective_message.reply_text(f'Could not send ZIP "{self.user_id} - {self.xth_zip.value}" for unknown reason').result()
                    self.logger.exception(e)
                self.xth_zip.value += 1
                self.current_size.value= 0
                self.next_zip.clear()
            self.current_size.value += size
            self.next_zip[id] = paths

        def send_as_zip(self, id, paths, index):
            with self.lock:
                self._send_as_zip(id, paths, index)

        def send_as_media(self, id, paths, index):
            try:
                self.logger.info(f'Sending {id} to user')
                self.parent._send_to_user(id, paths, self.bot, self.update, prefix=f'{index}/{self.total} ')
            except Exception as e:
                self.logger.exception(e)
                self.fail(id, index)

        def fail(self, id, index, reason='send'):
            self.update.effective_message.reply_markdown(f'Could not {reason} post {index}/{self.total} [{id}]({self.parent.post_link.format(id)})')

        def send(self, data):
            index, (id, paths) = data
            if not paths:
                self.fail(id, index, 'download')
                return

            if self.zip_it:
                self.send_as_zip(id, paths, index)
            else:
                self.send_as_media(id, paths, index)

    def _download_worker(self, download_queue: JoinableQueue, send_queue: JoinableQueue):
        for index, illust in iter(download_queue.get, 'STOP'):
            data_set = self._simple_download(illust)
            send_queue.put((index, data_set))
            download_queue.task_done()

    def _send_worker(self, send_queue: JoinableQueue, sender: Sender):
        for data_set in iter(send_queue.get, 'STOP'):
            sender.send(data_set)
            send_queue.task_done()

    def _download_all_of_user(self, bot, update, user_id, zip_it=False):
        illusts = []
        total_before = -1
        update.effective_message.reply_text(f'Collecting posts of user {user_id}').result()
        while len(illusts) % 30 == 0 and not total_before == len(illusts):
            total_before = len(illusts)
            self.logger.info(f'Collecting posts of user "{user_id}" - offset {total_before}')
            result = self.client.api.user_illusts(user_id, filter=None, req_auth=True, offset=total_before)
            illusts += result['illusts']
            if not illusts:
                update.effective_message.reply_text('No works found for given user').result()
                return

        total = len(illusts)
        update.effective_message.reply_text(f'Downloading {total} works (there can be multiple images per work) from {user_id}').result()
        self.logger.info(f'Start downloading {user_id}\'s posts')

        sender = self.Sender(self, zip_it, user_id, total, update, bot)
        download_queue = JoinableQueue()
        send_queue = JoinableQueue()

        for index, illust in enumerate(illusts, 1):
            download_queue.put((index, illust))

        for i in range(4):
            # Downloader
            Process(target=self._download_worker, args=(download_queue, send_queue)).start()
            # Sender
            Thread(target=self._send_worker, args=(send_queue, sender)).start()

        download_queue.join()
        send_queue.join()

        self.logger.info(f'All {user_id} posts have been sent')
        update.effective_message.reply_text(f'All {total} posts for user {user_id} have been sent')

command = Command()
