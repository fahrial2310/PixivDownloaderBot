from io import BytesIO
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

    def _chunks(self, l, n):
        """Yield successive n-sized chunks from l.
        https://stackoverflow.com/a/312464
        """
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def _file_to_bytes(self, path: Path) -> BytesIO:
        bytes = BytesIO()
        bytes.write(path.read_bytes())
        bytes.seek(0)
        return bytes

    @run_async
    def downloader(self, bot: Bot, update: Update):
        chat_id = update.effective_message.chat_id
        message = update.effective_message
        message_id = message.message_id
        url = message.text
        if not url:
            message.reply_text('No URL or ID supplied')
            return

        with TemporaryDirectory() as dir:
            try:
                downloads = self.client.download_by_url(url, dir)
                message.reply_text('Downloading...', reply_to_message_id=message_id)
                downloads = list(downloads)
            except PixivDownloaderError:
                message.reply_text(f'Post ({url}) not found', reply_to_message_id=message_id, link_preview=False)
                return

            if len(downloads) == 1:
                download = downloads[0]
                if download.suffix == '.mp4':
                    bot.send_video(chat_id, self._file_to_bytes(download), reply_to_message_id=message_id)
                else:
                    bot.send_photo(chat_id, self._file_to_bytes(download), reply_to_message_id=message_id)
            else:
                for chunk in self._chunks(downloads, 10):
                    media_group = map(InputMediaPhoto, map(self._file_to_bytes, chunk))
                    bot.send_media_group(chat_id, media_group, reply_to_message_id=message_id)


command = Command()
