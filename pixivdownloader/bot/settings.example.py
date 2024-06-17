import logging

TELEGRAM_API_TOKEN = '7252516378:AAHj2Ue8PYqm5iM9CWJTh3txQMmgu1-rJDg'
BASE_URL =  f'https://some.url/{TELEGRAM_API_TOKEN}'

ADMINS = ['@sengklek_ais']

PIXIV_USERNAME = 'user_gkfw3328'
PIXIV_PASSWORD = 'fahri2310'
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
