from io import BytesIO
from itertools import chain, islice
from pathlib import Path
from urllib.parse import urlparse
import logging
import re

from pixiv.downloader import PixivDownloader, PixivDownloaderError
from telegram import Bot, Update, InputMediaPhoto
from telegram.ext import run_async, MessageHandler, Filters

from pixivdownloader.bot.bot import main_bot
from pixivdownloader.bot.settings import PIXIV_USERNAME, PIXIV_PASSWORD, URL, DOWNLOAD_TO


class Command:
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

    @run_async
    def _download(self, bot, update, id, downloading_message=True):
        chat_id = update.effective_chat.id
        message = update.effective_message
        message_id = message.message_id

        downloadin_msg = None

        post = None
        if isinstance(id, dict):
            post = id
            id = str(id['id'])

        out_path = self.out_dir / id
        out_path.mkdir(exist_ok=True)
        downloads = list(out_path.glob('*.*'))

        if not downloads:
            try:
                if post:
                    downloads = self.client.download(post, out_path)
                else:
                    downloads = self.client.download_by_id(id, out_path)

                if downloading_message:
                    downloadin_msg = message.reply_markdown(f'Downloading `{id}`', reply_to_message_id=message_id)
                    downloadin_msg = downloadin_msg.result()
            except PixivDownloaderError:
                message.reply_markdown(f'Post `{id}` not found', reply_to_message_id=message_id)
                return

        for index, chunk in enumerate(self._chunks(downloads, 10)):
            self.logger.info(f'Getting chuck {index} of {id}')
            works = list(chunk)
            for jindex, work in enumerate(works[:]):
                new_work = work.parent / f'p{id}-{jindex}{work.suffix}'
                work.rename(new_work)
                works[jindex] = new_work

            if len(works) == 1:
                work = works[0]
                if work.suffix == '.mp4':
                    bot.send_video(chat_id, self._file_to_upload(work), reply_to_message_id=message_id, caption=id,
                                   timeout=60)
                else:
                    bot.send_photo(chat_id, self._file_to_upload(work), reply_to_message_id=message_id, caption=id)
            else:
                media_group = []
                for kindex, illustration in enumerate(works):
                    media_group.append(InputMediaPhoto(self._file_to_upload(illustration),
                                                       caption=f'{id} - {kindex + 1:0>2}'))
                bot.send_media_group(chat_id, media_group, reply_to_message_id=message_id,
                                     timeout=120, caption=id)

        if downloadin_msg:
            downloadin_msg.delete()

    def downloader(self, bot: Bot, update: Update):
        urls = update.effective_message.text

        if not urls:
            update.effective_message.reply_text('No URL or ID supplied')
            return

        for url in urls.split('\n'):
            ids = re.findall('(\\d+)', urlparse(url).path)
            for id in ids:
                self._download(bot, update, id)

    def all_from_user(self, bot: Bot, update: Update):
        url = update.effective_message.text
        id = re.findall('(\\d+)', url)[0]

        illusts = []
        while len(illusts) % 30 == 0:
            result = self.client.api.user_illusts(id, filter=None, req_auth=True, offset=len(illusts))
            illusts += result['illusts']
            if not illusts:
                self.message.reply_text('No works found for given user')
                return

        update.effective_message.reply_text(f'Downloading {len(illusts)} works (there can be multiple images per work:')
        for illustration in illusts:
            self._download(bot, update, illustration, downloading_message=False)
        # update.effective_message.reply_text(f'Finished sending all works of "{illusts[0]["user"]["name"]}".')


command = Command()
