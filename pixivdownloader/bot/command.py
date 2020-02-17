from io import BytesIO
from itertools import chain, islice
from multiprocessing import Pool
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

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

        self.client = PixivDownloader(username=PIXIV_USERNAME, password=PIXIV_PASSWORD)
        self.out_dir = Path(DOWNLOAD_TO)
        self.out_dir.mkdir(parents=True, exist_ok=True)

        main_bot.add_command(name='start', func=self.start)
        main_bot.add_command(MessageHandler, func=self.all_from_user,
                             filters=Filters.text & Filters.regex(re.compile('https://www.pixiv.net/en/users/\\d+', flags=re.IGNORECASE)))
        main_bot.add_command(MessageHandler, func=self.downloader, filters=Filters.text)

    def start(self, bot: Bot, update: Update):
        update.message.reply_markdown("""Hey,

I am here to help you download posts from [Pixiv](https://pixiv.net/).
Just send me a link or the id of the post and I'll give you the images / videos.
""")

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
        size = path.stat().st_size / 1024 / 1024  # In MB
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

        if not text:
            message.reply_text('No URL or ID supplied')
            return

        ids = re.findall('(\\d+)', text.replace('\n', ' '))
        total = len(ids)
        for index, id in enumerate(ids, 1):
            downloadin_msg = message.reply_markdown(f'Downloading `{id}`', reply_to_message_id=message.message_id)
            downloadin_msg = downloadin_msg.result()

            try:
                id, paths = self._simple_download(id)
                self._send_to_user(id, paths, bot, update, prefix=f'{index}/{total} ' if total > 1 else '')
            except PixivDownloaderError:
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
                media_group = []
                for jindex, path in enumerate(works, 1):
                    jkwargs = {
                        'caption': kwargs['caption'] + f' - {jindex}',
                        'parse_mode': kwargs['parse_mode'],
                    }
                    media_group.append(InputMediaPhoto(self._file_to_upload(path), **jkwargs))

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
        url = update.effective_message.text
        zip_it = 'zip' in url

        ids = re.findall('(\\d+)', url.replace('\n', ' '))
        for id in ids:
            self._download_all_of_user(bot, update, id, zip_it=zip_it)

    def _download_all_of_user(self, bot, update, user_id, zip_it=False):
        illusts = []
        while len(illusts) % 30 == 0:
            result = self.client.api.user_illusts(user_id, filter=None, req_auth=True, offset=len(illusts))
            illusts += result['illusts']
            if not illusts:
                update.effective_message.reply_text('No works found for given user')
                return

        total = len(illusts)
        update.effective_message.reply_text(f'Downloading {total} works (there can be multiple images per work) from {user_id}')

        next_zip = {}
        xth_zip = 1
        current_size = 0
        pool = Pool(4)
        for index, (id, paths) in enumerate(pool.imap(self._simple_download, illusts), 1):
            if zip_it:
                size = sum(map(lambda path: path.stat().st_size, paths))
                size = size / 1024 / 1024

                if current_size + size >= 50 or index == total:
                    self._send_as_zip(chain(*next_zip.values()), f'{user_id} - {xth_zip}.zip', update,
                                      caption=f'{index}/{total}', additional_files={
                                          'posts.txt': '\n'.join(map(str, next_zip.keys())) + '\n'
                                      })
                    xth_zip += 1
                    current_size = 0
                    next_zip = {}
                else:
                    current_size += size
                    next_zip[id] = paths
            else:
                try:
                    self._send_to_user(id, paths, bot, update, prefix=f'{index}/{total} ')
                except Exception as e:
                    update.effective_message.reply_text(f'Could not download/send post "{id}"')
                    self.logger.exception(e)
        pool.close()
        pool.join()


command = Command()
