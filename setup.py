from setuptools import find_packages, setup

version = '0.0.1.dev0'

setup(name='PixivDownloaderBot',
      version=version,
      description='I download pixiv posts for you, incl. videos',
      long_description=f'{open("README.rst").read()}\n{open("CHANGELOG.rst").read()}',

      author='Nachtalb',
      url='https://github.com/Nachtalb/PixivDownloaderBot',
      license='GPL3',

      packages=find_packages(exclude=['ez_setup']),
      namespace_packages=['pixivdownloader'],
      include_package_data=True,
      zip_safe=False,

      install_requires=[
          'mr.developer',
          'python-telegram-bot',
          'pixivdownloader',
          'requests_html',
          'opencv-python',
          'Pillow',
      ],

      entry_points={
          'console_scripts': [
              'bot = pixivdownloader.bot.bot:main',
          ]
      })
