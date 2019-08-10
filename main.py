import threading
import signal
import os
import sys
import time
import datetime
from dateutil.parser import parse
import sqlite3
import json
import logging
import structlog
from pyicloud import PyiCloudService
from googleapiclient.discovery import build
import google.oauth2.credentials
from google.auth.transport.requests import AuthorizedSession
from justbackoff import Backoff


MAX_IDLE_SECONDS = 300.0
ARTIFACTS_TABLE = 'artifacts'
AUTH_DIR = os.getenv('AUTH_DIR', './auth')
STORAGE_DIR = os.getenv('STORAGE_DIR', './downloaded')
DATABASE_FILE = os.getenv('DATABASE_FILE', 'artifacts.sqlite')


def initialize(logger_):
    logger = logger_.bind(database=DATABASE_FILE)

    db = sqlite3.connect(
        DATABASE_FILE, detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None)
    logger.info('Connected to database')

    logger = logger.bind(table=ARTIFACTS_TABLE)

    try:
        cursor = db.cursor()
        cursor.execute(
            f'CREATE TABLE {ARTIFACTS_TABLE} (id TEXT UNIQUE, name TEXT, size INTEGER, created INTEGER, downloaded INTEGER DEFAULT 0, uploaded TEXT DEFAULT NULL, album INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)')
    except sqlite3.OperationalError as e:
        if str(e).endswith('already exists'):
            logger.info('Table exists')
        else:
            logger.fatal('Failed to create table', exc_info=e)
            sys.exit(-1)
    else:
        logger.info('Table created')


def _loadGcloudCredentials():
    filePath = os.path.join(AUTH_DIR, 'gcloud.json')
    credentials = google.oauth2.credentials.Credentials.from_authorized_user_file(
        filePath)
    return credentials


class Connection:
    def __init__(self):
        self.connection = None

    def connect(self):
        self.connection = sqlite3.connect(
            DATABASE_FILE, detect_types=sqlite3.PARSE_DECLTYPES, isolation_level=None)
        return self.connection

    def disconnect(self):
        self.connection.close()


class Worker (threading.Thread):
    def __init__(self, name, logger):
        super().__init__()
        self.name = name
        self.logger = logger.bind(worker=name)
        self.exit = False
        self.event = threading.Event()
        self.backoff = Backoff(min_ms=100, max_ms=30000,
                               factor=2, jitter=False)
        self.lastWorked = time.time()

    def run(self):
        self.logger.info('Worker started')
        self.setup()
        while not self.exit:
            worked = False
            try:
                worked = self.work()
            except Exception as e:
                self.logger.warn('Worker job failed', exc_info=e)

            if worked:
                self.backoff.reset()
                self.lastWorked = time.time()
            else:
                #self.logger.debug(f'Worker has nothing to do')
                pass
            self.event.wait(self.backoff.duration())
            self.event.clear()

        self.teardown()
        self.logger.info('Worker stopped')

    def stop(self):
        self.exit = True
        self.event.set()

    def setup(self):
        pass

    def teardown(self):
        pass

    def work(self):
        return False

    def idle(self):
        return time.time() - self.lastWorked


class DatabaseWorker (Worker):
    def __init__(self, name, logger):
        super().__init__(name, logger)
        self.connection = Connection()

    def setup(self):
        self.db = self.connection.connect()

    def teardown(self):
        self.connection.disconnect()
        self.db = None


class DatabaseQueryWorker (DatabaseWorker):
    def __init__(self, name, query, logger, batch=False):
        super().__init__(name, logger)
        self.query = query
        self.batch = batch

    def work(self):
        cursor = self.db.cursor()
        try:
            cursor.execute(self.query)
            results = cursor.fetchall()
            if len(results) > 0:
                try:
                    if self.batch:
                        self.logger.debug(
                            f'Processing {len(results)} results in one batch')
                        self.process(results)
                    else:
                        self.logger.debug(f'Processing {len(results)} results')
                        for result in results:
                            if not self.exit:
                                self.process(result)
                    return True
                except Exception as e:
                    self.logger.warn(f'Failed to process result', exc_info=e)
                    return False
            else:
                return False
        except Exception as e:
            self.logger.warn('Worker failed to work', exc_info=e)
            return False

    def process(self, result):
        pass


class IcloudPhotoDownloader (DatabaseWorker):
    def __init__(self, logger):
        super().__init__('ICloud photo library scraper', logger)
        self.backoff = Backoff(min_ms=1000, max_ms=60000,
                               factor=2, jitter=False)
        self.iterator = None
        self.current = None
        if os.stat(STORAGE_DIR) is None:
            os.mkdir(STORAGE_DIR)
        with open(os.path.join(AUTH_DIR, 'icloud.json'), 'r') as file:
            credentials = json.load(file)
        self._icloud = PyiCloudService(
            credentials['username'], password=credentials['password'], cookie_directory=AUTH_DIR)

    def work(self):
        if self.current is None:
            try:
                self._next()
            except Exception as e:
                self.logger.warn(
                    'Failed to get listing from icloud', reason=e.message)
                return False

        if not self._downloaded(self.current):
            self.logger.warn('Downloading', photo=self.current.id)
            try:
                self._download(self.current)
            except:
                self.logger.warn('Download failed',
                                 photo=self.current.id, exc_info=e)
            else:
                self.logger.info('Download complete', photo=self.current.id)
                self.current = None
            return True
        else:
            self.current = None
            return False

    def _next(self):
        while self.current is None:
            if self.iterator is None:
                self.logger.info('Getting photo iterator')
                self.iterator = iter(self._icloud.photos.all)

            self.current = next(self.iterator, None)
            if self.current is None:
                self.iterator = None

    def _downloaded(self, photo):
        result = False
        c = self.db.cursor()
        c.execute(
            f'SELECT downloaded FROM {ARTIFACTS_TABLE} WHERE id=?', (photo.id,))
        r = c.fetchone()
        if r is not None:
            result = r[0] != 0
        else:
            c.execute(f'INSERT INTO {ARTIFACTS_TABLE} (id, name, size, created) VALUES (?, ?, ?, ?)', (
                photo.id, photo.filename, photo.size, int(photo.created.timestamp())))
        c.close()
        return result

    def _download(self, photo):
        c = self.db.cursor()
        c.execute(
            f'SELECT ROWID FROM {ARTIFACTS_TABLE} WHERE id=?', (photo.id,))
        r = c.fetchone()
        if r is not None:
            with open(f'{STORAGE_DIR}/{r[0]}.dat', 'wb') as f:
                download = photo.download()
                for chunk in download.iter_content(chunk_size=4096):
                    if chunk:
                        f.write(chunk)
                f.flush()
            c.execute(
                f'UPDATE {ARTIFACTS_TABLE} SET downloaded=1 WHERE id=?', (photo.id,))


class GoogleUploader (DatabaseQueryWorker):
    def __init__(self, logger):
        super().__init__('Google photo uploader',
                         f'SELECT ROWID, * FROM {ARTIFACTS_TABLE} WHERE downloaded=1 AND uploaded is NULL', logger)
        self._session = AuthorizedSession(_loadGcloudCredentials())

    def process(self, result):
        index = result[0]
        id = result[1]
        name = result[2]
        filename = f'{STORAGE_DIR}/{index}.dat'
        with open(filename, 'rb') as f:
            self.logger.info('Uploading item', photo=id, file=filename)
            response = self._session.post('https://photoslibrary.googleapis.com/v1/uploads', data=f, headers={
                'Content-type': 'application/octet-stream',
                'X-Goog-Upload-File-Name': name,
                'X-Goog-Upload-Protocol': 'raw'
            })
            if response.status_code / 100 == 2:
                self.logger.info('Upload complete', photo=id, file=filename)
                c = self.db.cursor()
                c.execute(
                    f'UPDATE {ARTIFACTS_TABLE} SET uploaded=? WHERE id=?', (response.content, id))
            else:
                raise Exception('Upload failed', response.status_code)


class GoogleAlbumAppender (DatabaseQueryWorker):
    def __init__(self, album, logger):
        super().__init__('Google photo album appender',
                         f'SELECT * FROM {ARTIFACTS_TABLE} WHERE uploaded is not NULL AND album=0', logger, batch=True)
        self.title = album
        self.album = None
        self.logger = self.logger.bind(album=self.title)
        self._gphotos = build(
            'photoslibrary', 'v1', credentials=_loadGcloudCredentials(), cache_discovery=False)

    def process(self, results):
        self._setAlbumId()

        data = {
            'albumId': self.album['id'],
            'newMediaItems': list(map(lambda row: {
                'description': row[1],
                'simpleMediaItem': {
                    'uploadToken': row[5].decode("ascii")
                }
            }, results))
        }

        response = self._gphotos.mediaItems().batchCreate(body=data).execute()

        count = 0
        c = self.db.cursor()
        for item in response['newMediaItemResults']:
            if item['status']['message'] == 'OK':
                c.execute(f'UPDATE {ARTIFACTS_TABLE} SET album=1 WHERE uploaded=?',
                          (item['uploadToken'].encode('ascii'),))
                count += 1
        if count > 0:
            self.logger.info(f'Added {count} items to album')

    def _setAlbumId(self):
        if self.album is None:
            results = self._gphotos.albums().list().execute()
            albums = results.get('albums', [])
            for album in albums:
                if album.get('title') == self.title:
                    self.album = album
                    self.logger.info('Found album', album=self.album['id'])
                    return
            self.album = gphotos.albums().create(
                body={'album': {'title': self.title}}).execute()
            self.logger.info('Created album')


class Cleaner (DatabaseQueryWorker):
    def __init__(self, logger):
        super().__init__('Cleaner',
                         f'SELECT ROWID FROM {ARTIFACTS_TABLE} WHERE album=1 AND deleted=0', logger)

    def process(self, result):
        id = result[0]
        filename = f'{STORAGE_DIR}/{id}.dat'
        os.remove(filename)
        self.logger.info('Removed download artifact', file=filename)
        c = self.db.cursor()
        c.execute(
            f'UPDATE {ARTIFACTS_TABLE} SET deleted=1 WHERE ROWID=?', (id,))


class ProgressLogger:
    def __init__(self, logger):
        self.logger = logger.bind(worker='Progress logger')
        self.connection = Connection()

    def start(self):
        self.db = self.connection.connect()
        self.cursor = self.db.cursor()

    def stop(self):
        self.connection.disconnect()
        self.cursor = None
        self.db = None

    def emit(self):
        self.cursor.execute(
            f'SELECT (SELECT count(ROWID) FROM {ARTIFACTS_TABLE} WHERE deleted=1) AS completed, (SELECT count(ROWID) FROM {ARTIFACTS_TABLE} WHERE downloaded <> 0 AND uploaded IS NOT NULL) AS uploaded, (SELECT count(ROWID) FROM {ARTIFACTS_TABLE} WHERE downloaded <> 0) AS downloaded')
        results = self.cursor.fetchall()[0]
        completed = results[0]
        uploaded = results[1]
        downloaded = results[2]
        self.logger.info('Progress', downloaded=downloaded,
                         uploaded=uploaded, completed=completed)
        return True


def main():
    logger = structlog.get_logger()
    # logging.basicConfig(level=logging.DEBUG)

    workers = []
    run = True

    def stop(signum, stack):
        nonlocal run
        run = False
        print()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    initialize(logger)

    workers.append(IcloudPhotoDownloader(logger))
    workers.append(GoogleUploader(logger))
    workers.append(GoogleAlbumAppender('From ICloud', logger))
    workers.append(Cleaner(logger))

    for worker in workers:
        worker.start()

    progressLogger = ProgressLogger(logger)
    progressLogger.start()

    while run:
        minIdle = min(list(map(lambda w: w.idle(), workers)))
        if (minIdle > MAX_IDLE_SECONDS):
            logger.info(
                f'All workers have been idle for more than {MAX_IDLE_SECONDS} seconds, we are done.')
            break
        time.sleep(10.0)
        progressLogger.emit()

    progressLogger.stop()

    for worker in workers:
        worker.stop()

    for worker in workers:
        worker.join()


if __name__ == "__main__":
    main()
