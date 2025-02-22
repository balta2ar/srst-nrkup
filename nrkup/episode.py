import asyncio
import json
import logging
import re
import shutil
from asyncio import TimeoutError as AsyncioTimeoutError
from asyncio import create_subprocess_shell
from bisect import bisect_right
from datetime import datetime
from glob import glob
from itertools import groupby
from os import makedirs
from os.path import dirname, exists, expanduser, expandvars, getsize, isfile, join
from shlex import quote
from tempfile import NamedTemporaryFile
from typing import List

import hachoir
import pysubs2
from bs4 import BeautifulSoup
from diskcache import Cache


def expand(path):
    return expanduser(expandvars(path))

CACHE_DIR = expand('~/.cache/nrkup')
BASE = expand('~/payload/video/nrkup/nordland')

async def async_http_get(url, timeout=10.0):
    async def request():
        logging.info('GET %s', url)
        USER_AGENT = 'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:100.0) Gecko/20100101 Firefox/100.0'
        headers = {'User-Agent': USER_AGENT}
        retries = 3
        async with ClientSession() as session:
            for i in range(retries):
                try:
                    async with session.get(url, timeout=timeout, headers=headers) as resp:
                        return await resp.text()
                except AsyncioTimeoutError as e:
                    logging.warning('timeout (%s) getting "%s": "%s"', timeout, url, e)
                    if i == retries - 1:
                        raise
    cache = Cache(CACHE_DIR)
    if url not in cache: cache[url] = await request()
    result = cache[url]
    cache.close()
    return result


async def async_run(args):
    cmd = ' '.join(quote(x) for x in args)
    logging.info('Running: %s', cmd)
    proc = await create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise Exception(stderr.decode())


def which(program):
    paths = [None, '/usr/local/bin', '/usr/bin', '/bin', '~/bin', '~/.local/bin']
    for path in paths:
        path = expanduser(expandvars(path)) if path else None
        path = shutil.which(program, path=path)
        if path:
            return path


def slurp(where, js=False):
    with open(where) as f:
        what = f.read()
        if js: what = json.loads(what)
        return what


def spit(where, what, js=False):
    if not exists(dirname(where)): makedirs(dirname(where), exist_ok=True)
    with open(where, 'w') as f:
        if js: what = json.dumps(what, indent=2)
        f.write(what)



async def ui_notify(title, message):
    await async_run([which('notify-send'), title, message])


async def detect_speakers(audio, output):
    await async_run([which('detect-speakers'), audio, '-o', output])


def find_first(pattern):
    files = glob(pattern, recursive=True)
    return files[0] if len(files) > 0 else None


async def nrk_download(url, where) -> str:
    makedirs(where, exist_ok=True)
    logging.info('Downloading %s to %s', url, where)
    await async_run([expand(which('nrkdownload')), '-d', where, url])
    m4v = find_first(where + '/**/*.m4v')
    if m4v is None: raise ValueError('No m4v found in %s' % where)
    return m4v


async def ffmpeg_extract_audio(video, audio):
    logging.info('converting %s to %s', video, audio)
    await async_run([which('ffmpeg'), '-i', video, '-q:a', '0', '-map', 'a', audio])


async def sox_compress_dynamic_range(input, output):
    logging.info('compressing dynamic range %s, %s', input, output)
    await async_run([which('sox'), input, output, 'compand', '0.3,1', '6:-70,-60,-20', '-5', '-90', '0.2'])
    #await async_run(['sox', input, output, 'compand', '0.02,0.20', '5:-60,-40,-10', '-5', '-90', '0.1'])


async def sox_remove_silence(input, output):
    logging.info('removing silence %s, %s', input, output)
    await async_run([which('sox'), input, output, '-l', '1', '0.1', '1%', '-1', '1.0', '1%'])


def cleanup(text):
    lines = [x.strip() for x in text.splitlines()]
    lines = [x for x in lines if '-->' not in x]
    lines = [x for x in lines if re.search(r'^\d+$', x) is None]
    lines = [x for x in lines if re.search(r'^$', x) is None]
    return '\n'.join(lines)


def cleanup2(text):
    text = text.replace('\\N', ' ')
    text = text.replace('- -', '--')
    text = re.sub(r'-\n', '-', text)
    text = text.strip()
    return text




class SpeakerSection:
    def __init__(self, start: float, end: float, speaker: str):
        self.start: float = start
        self.end: float = end
        self.speaker: str = speaker
    def __repr__(self):
        return 'SpeakerSection(%s, %s, %s)' % (self.start, self.end, self.speaker)
    @staticmethod
    def from_json(data):
        return SpeakerSection(data['start'], data['end'], data['speaker'])
    # @staticmethod
    # def collapse(sections: List[SpeakerSection]) -> List[SpeakerSection]:
    #     sections = sorted(sections, key=lambda x: x.start)
    #     result: List[SpeakerSection] = []
    #     for section in sections:
    #         if len(result) == 0: section.start = 0.0
    #         if len(result) == 0 or result[-1].speaker != section.speaker:
    #             result.append(section)
    #         else:
    #             result[-1].end = max(result[-1].end, section.end)
    #     return result


class Speakers:
    def __init__(self, filename, timeline):
        self.filename = filename
        self.timeline = timeline
    @staticmethod
    def from_json(data):
        return Speakers(data['filename'],
                        [SpeakerSection.from_json(x) for x in data['timeline']])
    __repr__ = lambda self: 'Speakers(%s, %s)' % (self.filename, self.timeline)


class IndexPoint:
    def __init__(self, start: float, title: str):
        self.start: float = start
        self.title: str = title
    def __repr__(self):
        M = int(self.start) // 60
        S = int(self.start) % 60
        return '%02d:%02d %s' % (M, S, self.title)
    @staticmethod
    def from_json(data):
        # PT7M6.24S
        rx = re.compile(r'''
            ^PT
            ((?P<hours>\d+)H)?
            ((?P<minutes>\d+)M)?
            (?P<seconds>\d+(\.\d+)?)S
            $''',
            re.VERBOSE)
        m = rx.search(data['startPoint'])
        if m is None: raise ValueError('Invalid IndexPoint: %s' % data)
        H = int(m.group('hours') or '0')
        M = int(m.group('minutes') or '0')
        S = float(m.group('seconds') or '0')
        return IndexPoint(H*3600+M*60+S, data['title'])


class Srt:
    def __init__(self, raw: str, index_points: List[IndexPoint], timeline: List[SpeakerSection]):
        self.raw = raw
        self.index_points = sorted(index_points, key=lambda x: x.start)
        self.topic_times = [pysubs2.make_time(s=x.start) for x in self.index_points]
        self.timeline = sorted(timeline, key=lambda x: x.start)
        self.timeline_times = [pysubs2.make_time(s=x.start) for x in self.timeline]
    def topic(self, x: pysubs2.SSAEvent):
        return bisect_right(self.topic_times, x.start)
    def speaker(self, x: pysubs2.SSAEvent):
        index = bisect_right(self.timeline_times, x.start)
        index = min(index, len(self.timeline)-1)
        y = self.timeline[index]
        #print('SPEAKER', index, y.speaker, x.start, y.start, x.text)
        return y.speaker
    def subs(self) -> List[pysubs2.SSAEvent]:
        with NamedTemporaryFile(suffix='.srt') as f:
            f.write(self.raw.encode())
            f.flush()
            return pysubs2.load(f.name)
    def by_topic(self, subs: List[pysubs2.SSAEvent]):
        for k, g in groupby(subs, key=self.topic):
            g = sorted(g, key=lambda x: x.start)
            yield k, g
    def by_speaker(self, subs: List[pysubs2.SSAEvent]):
        for k, g in groupby(subs, key=self.speaker):
            g = sorted(g, key=lambda x: x.start)
            yield k, g
    def blocks(self):
        blocks = []
        subs = self.subs()
        for topic_index, g1 in self.by_topic(subs):
            #print('NEW TOPIC, INDEX =', topic_index)
            topic = []
            for speaker_index, g2 in self.by_speaker(g1):
                g2 = cleanup2(' '.join([cleanup2(x.text) for x in g2]))
                topic.append(g2)
                #speaker = self.timeline[speaker_index].speaker
                #print(speaker_index, speaker, g2)
                #print(speaker_index, g2)
            g1 = cleanup2('\n'.join(topic))
            block = str(topic_index) + ' ' + str(self.index_points[topic_index-1]) + '\n' + g1
            blocks.append(block)
            #print('-'*80)
        blocks = '\n\n'.join(blocks)
        return blocks




class NrkUrl:
    def __init__(self, typ, name, ym, dkno):
        self.typ = typ
        self.name = name
        self.ym = ym
        self.dkno = dkno
    def __repr__(self):
        return 'NrkUrl(%s, %s, %s, %s)' % (self.typ, self.name, self.ym, self.dkno)
    @staticmethod
    def from_url(url):
        # https://tv.nrk.no/serie/distriktsnyheter-nordland/202206/DKNO98061322/avspiller
        m = re.search(r'^https?://tv.nrk.no/(?P<type>\w+)/(?P<name>[^/]+)/(?P<ym>\d+)/(?P<dkno>DKNO\d+)(.+)?', url)
        if m: return NrkUrl(m.group('type'), m.group('name'), m.group('ym'), m.group('dkno'))
        # https://tv.nrk.no/serie/distriktsnyheter-nordland/sesong/202406/episode/DKNO98060624
        m = re.search(r'^https?://tv.nrk.no/serie/(?P<name>[^/]+)/sesong/(?P<ym>\d+)/episode/(?P<dkno>DKNO\d+)', url)
        if m: return NrkUrl('serie', m.group('name'), m.group('ym'), m.group('dkno'))
        # https://tv.nrk.no/se?v=DKNO98060624
        m = re.search(r'^https?://tv.nrk.no/se\?v=(?P<dkno>\w+)', url)
        if m: return NrkUrl('type_NA', 'name_NA', 'ym_NA', m.group('dkno'))
        raise ValueError('Invalid url: %s' % url)


async def fetch_nrk_metadata(dkno):
    url = 'https://psapi.nrk.no/playback/metadata/program/%s' % dkno
    return json.loads(await async_http_get(url))


def non_empty_file(path):
    return isfile(path) and getsize(path) > 0

def subtitles_url(url, host, port):
    return f'http://{host}:{port}/subtitles?url={url}'

class Episode:
    def __init__(self, title, url, date):
        self.title = title
        self.url = url
        self.nrkurl = NrkUrl.from_url(url)
        self.date = date
        self.short = self.date.strftime('%Y%m%d-%H%M')

    @property
    def day(self):
        return self.date.strftime('%Y%m%d')

    def srt(self):
        name = find_first(self.base + '/**/*.srt')
        if name is None: raise ValueError('No srt file found in %s' % self.base)
        return slurp(name)

    async def pretty_srt(self, host, port):
        index_points = await self.index_points()
        speakers = await self.speakers()
        srt = Srt(self.srt(), index_points, speakers.timeline)
        # topics.sort(key=lambda x: x.start)
        # times = [pysubs2.make_time(s=x.start) for x in topics]
        # def topic(x: pysubs2.SSAEvent): return bisect_right(times, x.start)
        # blocks = []
        # with NamedTemporaryFile(suffix='.srt') as f:
        #     f.write(self.srt().encode())
        #     f.flush()
        #     for k, g in groupby(pysubs2.load(f.name), key=topic):
        #         g = sorted(g, key=lambda x: x.start)
        #         g = '\n'.join([cleanup2(x.text) for x in g])
        #         g = cleanup2(g)
        #         blocks.append(str(k) + ' ' + str(topics[k-1]) + '\n' + g)
        # blocks = '\n\n'.join(blocks)
        blocks = srt.blocks()
        body = self.day + '\n' + self.url + '\n' + subtitles_url(self.url) + '\n\n' + blocks
        return body

    @property
    def metafile(self):
        return join(self.base, 'metadata.json')

    async def metadata(self):
        if not non_empty_file(self.metafile):
            await ui_notify('NRKUP', 'Fetching metadata: ' + self.nrkurl.dkno)
            data = await fetch_nrk_metadata(self.nrkurl.dkno)
            spit(self.metafile, data, js=True)
        return slurp(self.metafile, js=True)

    @property
    def speakersfile(self):
        return join(self.base, 'speakers.json')

    async def speakers(self):
        if not non_empty_file(self.speakersfile):
            await ui_notify('NRKUP', 'Detecting speakers: ' + self.nrkurl.dkno)
            await detect_speakers(await self.mp3(), self.speakersfile)
        data = slurp(self.speakersfile, js=True)
        return Speakers.from_json(data)

    async def index_points(self) -> List[IndexPoint]:
        data = await self.metadata()
        return [IndexPoint.from_json(x) for x in data['preplay']['indexPoints']]

    async def mp3(self):
        # if exists(self.audio):
        #     return self.audio
        await self.metadata()
        video = find_first(self.base + '/**/*.m4v')
        if not video:
            await ui_notify('NRKUP', 'nwkdownload: Downloading video: ' + self.title)
            video = await nrk_download(self.url, self.base)
        orig_audio = join(self.base, 'orig-' + self.name)
        if not exists(orig_audio):
            await ui_notify('NRKUP', 'ffmpeg: Extracting audio: ' + self.title)
            await ffmpeg_extract_audio(video, orig_audio)
        nosilence_audio = join(self.base, 'nosilence-' + self.name)
        if not exists(nosilence_audio):
            await ui_notify('NRKUP', 'sox: removing silence: ' + self.title)
            await sox_compress_dynamic_range(orig_audio, nosilence_audio)
        if not exists(self.audio):
            await ui_notify('NRKUP', 'sox: compressing dynamic range: ' + self.title)
            await sox_compress_dynamic_range(nosilence_audio, self.audio)

        return self.audio

    @property
    def base(self):
        return f'{BASE}/{self.short}-{self.nrkurl.dkno}'

    @property
    def audio(self):
        return join(self.base, self.name)

    @property
    def name(self):
        return f'nord-{self.short}.mp3'

    @staticmethod
    async def make(url):
        soup = BeautifulSoup(await async_http_get(url), 'html.parser')
        title = soup.find('title').text
        data = PageData(json.loads(soup.find('script', {'id': 'pageData'}).string))
        date = data.release_date
        #date = soup.find('meta', property='video:release_date').get('content')
        # 2022-06-01T22:55:00+02:00
        #date = datetime.strptime(date, '%Y-%m-%dT%H:%M:%S%z')
        return Episode(title, url, date)

    def __repr__(self):
        return f'<Episode title="{self.title}" url="{self.url}" date="{self.date}">'

class PageData:
    def __init__(self, data):
        self.data = data
    @property
    def release_date(self):
        id = self.data['initialState']['selectedEpisodePrfId']
        for season in self.data['initialState']['seasons']:
            for episode in season['episodes']:
                if episode['prfId'] == id:
                    return datetime.fromisoformat(episode['releaseDateOnDemand'].replace('[Date]', ''))
