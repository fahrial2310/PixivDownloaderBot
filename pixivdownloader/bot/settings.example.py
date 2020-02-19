import logging

TELEGRAM_API_TOKEN = ''
BASE_URL =  f'https://some.url/{TELEGRAM_API_TOKEN}'

ADMINS = ['@USERNAME']

PIXIV_USERNAME = ''
PIXIV_PASSWORD = ''
URL = BASE_URL + '/media'
DOWNLOAD_TO = '/some/path/to/files'

# More information about polling and webhooks can be found here:
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks
MODE = {
    'active': 'polling',  # "webook" or "polling"
    # 'configuration': {
    #     'listen': '127.0.0.1',
    #     'port': 5000,
    #     'url_path': TELEGRAM_API_TOKEN,
    #     'url': BASE_URL
    # },
}

LOG_LEVEL = logging.DEBUG
