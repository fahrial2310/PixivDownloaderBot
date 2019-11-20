from io import BytesIO
from itertools import chain, islice
from pathlib import Path
from tempfile import TemporaryDirectory
import logging

from pixiv.downloader import PixivDownloader, PixivDownloaderError
from telegram import Bot, Update, InputMediaPhoto
from telegram.ext import run_async, MessageHandler, Filters

from pixivdownloader.bot.bot import main_bot
from pixivdownloader.bot.settings import PIXIV_USERNAME, PIXIV_PASSWORD


class Command:
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

        self.client = PixivDownloader(username=PIXIV_USERNAME, password=PIXIV_PASSWORD)

        main_bot.add_command(name='start', func=self.start)
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

    @run_async
    def _download(self, bot, update, url):
        chat_id = update.effective_chat.id
        message = update.effective_message
        message_id = message.message_id

        with TemporaryDirectory() as dir:
            try:
                downloads = self.client.download_by_url(url, dir)
                downloadin_msg = message.reply_text(f'Downloading {url}', reply_to_message_id=message_id,
                                                          disable_web_page_preview=True)
                downloadin_msg = downloadin_msg.result()
            except PixivDownloaderError:
                message.reply_text(f'Post ({url}) not found', reply_to_message_id=message_id, disable_web_page_preview=True)
                return

            for index, chunk in enumerate(self._chunks(downloads, 10)):
                self.logger.info(f'Downloading chuck {index} of {url}')
                works = list(chunk)

                if len(works) == 1:
                    work = works[0]
                    if work.suffix == '.mp4':
                        bot.send_video(chat_id, self._file_to_bytes(work), reply_to_message_id=message_id, caption=url,
                                       timeout=60)
                    else:
                        bot.send_photo(chat_id, self._file_to_bytes(work), reply_to_message_id=message_id, caption=url)
                else:
                    media_group = map(InputMediaPhoto, map(self._file_to_bytes, works))
                    bot.send_media_group(chat_id, media_group, reply_to_message_id=message_id,
                                         timeout=120, caption=url)
            downloadin_msg.delete()

    def downloader(self, bot: Bot, update: Update):
        urls = update.effective_message.text

        if not urls:
            update.effective_message.reply_text('No URL or ID supplied')
            return

        for url in urls.split('\n'):
            self._download(bot, update, url)


command = Command()
