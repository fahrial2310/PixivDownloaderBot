from io import BytesIO
from itertools import chain, islice
from multiprocessing import Pool, Manager
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZipFile
import logging
import re

from pixiv.downloader import PixivDownloader, PixivDownloaderError
from telegram import Bot, Update, InputMediaPhoto, ParseMode
from telegram.ext import run_async, MessageHandler, Filters

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
        bytes = BytesIO()
        bytes.write(path.read_bytes())
        bytes.seek(0)
        return bytes

    def _with_url_possible(self, path: Path) -> bool:
        size = path.stat().st_size / 1000 / 1000  # In MB
        extension = path.suffix.lower().lstrip('.')
        return URL and (
            (extension in ['png', 'jpg', 'jpeg', 'webp'] and size <= 5)
            or
            (extension in ['mp4'] and size <= 20)
        )

    def _file_to_upload(self, path: Path) -> str or BytesIO:
        if self._with_url_possible(path):
            return URL.rstrip('/') + '/' + str(path.relative_to(self.out_dir))
        else:
            return self._file_to_bytes(path)

    def downloader(self, bot: Bot, update: Update):
        message = update.effective_message
        text = message.text
        tried_relogin = False

        if not text:
            message.reply_text('No URL or ID supplied')
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
        for index, path in enumerate(downloader):
            new_path = path.parent / f'p{post_id}-{index}{path.suffix}'
            path.rename(new_path)
            resultset.append(new_path)
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
                if not tried_relogin:
                    try:
                        self.login()
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
                    self.parent._send_as_zip(chain(*self.next_zip.values()), f'{self.user_id} - {self.xth_zip.value}.zip',
                                        self.update,
                                      caption=f'{post}/{self.total}', additional_files={
                                          'posts.txt': '\n'.join(map(str, self.next_zip.keys())) + '\n'
                                      })

                    if send_last_lonely:
                        self.logger.info(f'Sending zip with {self.next_zip.keys()}')
                        self._send_as_zip(paths, f'{self.user_id} - {self.xth_zip.value + 1}.zip', self.update,
                                      caption=f'{self.total}/{self.total}', additional_files={
                                          'posts.txt': str(id) + '\n'
                                      })
                except Exception as e:
                    self.update.effective_message.reply_text(f'Could not send ZIP "{self.user_id} - {self.xth_zip.value}" for unknown reason')
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
                self.update.effective_message.reply_text(f'Could not download/send post "{self.parent.post_link.format(id)}"')
                self.logger.exception(e)

        def send(self, data):
            index, (id, paths) = data

            if self.zip_it:
                self.send_as_zip(id, paths, index)
            else:
                self.send_as_media(id, paths, index)

    def _download_all_of_user(self, bot, update, user_id, zip_it=False):
        illusts = []
        total_before = -1
        update.effective_message.reply_text(f'Collecting posts of user {user_id}')
        while len(illusts) % 30 == 0 and not total_before == len(illusts):
            total_before = len(illusts)
            self.logger.info(f'Collecting posts of user "{user_id}" - offset {total_before}')
            result = self.client.api.user_illusts(user_id, filter=None, req_auth=True, offset=total_before)
            illusts += result['illusts']
            if not illusts:
                update.effective_message.reply_text('No works found for given user')
                return

        total = len(illusts)
        update.effective_message.reply_text(f'Downloading {total} works (there can be multiple images per work) from {user_id}')
        self.logger.info(f'Start downloading {user_id}\'s posts')

        sender = self.Sender(self, zip_it, user_id, total, update, bot)

        download_pool = Pool(4)
        sender_pool = Pool(4)

        class DownloadIter:
            def __init__(self, iterable, length):
                self.iterable = iterable
                self.length = length

            def __len__(self):
                return self.length

            def __iter__(self):
                return self.iterable

            def __next__(self):
                return next(self.iterable)

        download_iter = DownloadIter(
            enumerate(download_pool.imap(self._simple_download, illusts, 4), 1),
            total)

        sender_pool.map(sender.send, download_iter, 4)

        self.logger.info(f'All {user_id} posts have been sent')
        update.effective_message.reply_text(f'All {total} posts for user {user_id} have been sent')

        sender_pool.close()
        download_pool.close()

        sender_pool.join()
        download_pool.join()


command = Command()
