import platform
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import aiohttp
from aiohttp import ClientTimeout
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import pytest
from pytest_localserver.http import WSGIServer
from parfive.downloader import Downloader, Token, FailedDownload, Results
from parfive.utils import sha256sum
import parfive
import sys
import os
import hashlib

skip_windows = pytest.mark.skipif(platform.system() == 'Windows', reason="Windows.")


def test_setup():
    dl = Downloader()

    assert isinstance(dl, Downloader)

    assert len(dl.http_queue) == 0
    assert len(dl.http_tokens) == 5
    assert len(dl.ftp_queue) == 0
    assert len(dl.ftp_tokens) == 5


def test_download(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T',
                             headers={'Content-Disposition': "attachment; filename=testfile.fits"})
    dl = Downloader()

    dl.enqueue_file(httpserver.url, path=Path(tmpdir), max_splits=None)

    assert dl.queued_downloads == 1

    f = dl.download()

    assert len(f) == 1
    assert Path(f[0]).name == "testfile.fits"
    assert sha256sum(f[0]) == "a1c58cd340e3bd33f94524076f1fa5cf9a7f13c59d5272a9d4bc0b5bc436d9b3"


def test_download_ranged_http(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T',
                             headers={'Content-Disposition': "attachment; filename=testfile.fits"})
    dl = Downloader()

    dl.enqueue_file(httpserver.url, path=Path(tmpdir))

    assert dl.queued_downloads == 1

    f = dl.download()

    assert len(f) == 1
    assert Path(f[0]).name == "testfile.fits"
    assert sha256sum(f[0]) == "a1c58cd340e3bd33f94524076f1fa5cf9a7f13c59d5272a9d4bc0b5bc436d9b3"


def test_download_partial(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T')
    dl = Downloader()

    dl.enqueue_file(httpserver.url, filename=lambda resp, url: Path(tmpdir) / "filename")
    f = dl.download()
    assert len(f) == 1

    # strip the http://
    assert "filename" in f[0]


def test_empty_download(tmpdir):
    dl = Downloader()

    f = dl.download()
    assert len(f) == 0


def test_download_filename(httpserver, tmpdir):
    httpserver.serve_content('SIMPLE  = T')

    fname = "testing123"
    filename = str(tmpdir.join(fname))
    with open(filename, "w") as fh:
        fh.write("SIMPLE = T")

    dl = Downloader()

    dl.enqueue_file(httpserver.url, filename=filename, chunksize=200)
    f = dl.download()

    assert isinstance(f, Results)
    assert len(f) == 1

    assert f[0] == filename


def test_download_no_overwrite(httpserver, tmpdir):
    httpserver.serve_content('SIMPLE  = T')

    fname = "testing123"
    filename = str(tmpdir.join(fname))
    with open(filename, "w") as fh:
        fh.write("Hello world")

    dl = Downloader()

    dl.enqueue_file(httpserver.url, filename=filename, chunksize=200)
    f = dl.download()

    assert isinstance(f, Results)
    assert len(f) == 1

    assert f[0] == filename

    with open(filename) as fh:
        # If the contents is the same as when we wrote it, it hasn't been
        # overwritten
        assert fh.read() == "Hello world"


def test_download_overwrite(httpserver, tmpdir):
    httpserver.serve_content('SIMPLE  = T')

    fname = "testing123"
    filename = str(tmpdir.join(fname))
    with open(filename, "w") as fh:
        fh.write("Hello world")

    dl = Downloader(overwrite=True)

    dl.enqueue_file(httpserver.url, filename=filename, chunksize=200)
    f = dl.download()

    assert isinstance(f, Results)
    assert len(f) == 1

    assert f[0] == filename

    with open(filename) as fh:
        assert fh.read() == "SIMPLE  = T"


def test_download_unique(httpserver, tmpdir):
    httpserver.serve_content('SIMPLE  = T')

    fname = "testing123"
    filename = str(tmpdir.join(fname))

    filenames = [filename, filename+'.fits', filename+'.fits.gz']

    dl = Downloader(overwrite='unique')

    # Write files to both the target filenames.
    for fn in filenames:
        with open(fn, "w") as fh:
            fh.write("Hello world")

            dl.enqueue_file(httpserver.url, filename=fn, chunksize=200)

    f = dl.download()

    assert isinstance(f, Results)
    assert len(f) == len(filenames)

    for fn in f:
        assert fn not in filenames
        assert f"{fname}.1" in fn


@pytest.fixture
def testserver(request):
    """A server that throws a 404 for the second request"""
    counter = 0

    def simple_app(environ, start_response):
        """Simplest possible WSGI application"""
        nonlocal counter

        counter += 1
        if counter != 2:
            status = '200 OK'
            response_headers = [('Content-type', 'text/plain'),
                                ('Content-Disposition', (f'testfile_{counter}'))]
            start_response(status, response_headers)
            return [b'Hello world!\n']
        else:
            status = '404'
            response_headers = [('Content-type', 'text/plain')]
            start_response(status, response_headers)
            return ""

    server = WSGIServer(application=simple_app)
    server.start()
    request.addfinalizer(server.stop)
    return server


def test_retrieve_some_content(testserver, tmpdir):
    """
    Test that the downloader handles errors properly.
    """
    tmpdir = str(tmpdir)
    dl = Downloader()

    nn = 5
    for i in range(nn):
        dl.enqueue_file(testserver.url, path=tmpdir)

    f = dl.download()

    assert len(f) == nn - 1
    assert len(f.errors) == 1


def test_no_progress(httpserver, tmpdir, capsys):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T')
    dl = Downloader(progress=False)

    dl.enqueue_file(httpserver.url, path=tmpdir)

    dl.download()

    # Check that there was not stdout
    captured = capsys.readouterr().out
    assert not captured


def throwerror(*args, **kwargs):
    raise ValueError("Out of Cheese.")


@patch("parfive.downloader.default_name", throwerror)
def test_raises_other_exception(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T')
    dl = Downloader()

    dl.enqueue_file(httpserver.url, path=tmpdir)
    res = dl.download()
    assert isinstance(res.errors[0].exception, ValueError)


def test_token():
    t = Token(5)

    assert "5" in repr(t)
    assert "5" in str(t)


def test_failed_download():
    err = FailedDownload("wibble", "bbc.co.uk", "running away")

    assert "bbc.co.uk" in repr(err)
    assert "bbc.co.uk" in repr(err)

    assert "running away" in str(err)
    assert "running away" in str(err)


def test_results():
    res = Results()

    res.append("hello")

    res.add_error("wibble", "notaurl", "out of cheese")

    assert "notaurl" in repr(res)
    assert "hello" in repr(res)
    assert "out of cheese" in repr(res)


def test_notaurl(tmpdir):
    tmpdir = str(tmpdir)

    dl = Downloader(progress=False)

    dl.enqueue_file("http://notaurl.wibble/file", path=tmpdir)

    f = dl.download()

    assert len(f.errors) == 1
    assert isinstance(f.errors[0].exception, aiohttp.ClientConnectionError)


def test_retry(tmpdir, testserver):
    tmpdir = str(tmpdir)
    dl = Downloader()

    nn = 5
    for i in range(nn):
        dl.enqueue_file(testserver.url, path=tmpdir)

    f = dl.download()

    assert len(f) == nn - 1
    assert len(f.errors) == 1

    f2 = dl.retry(f)

    assert len(f2) == nn
    assert len(f2.errors) == 0


def test_empty_retry():
    f = Results()
    dl = Downloader()

    dl.retry(f)


@skip_windows
@pytest.mark.allow_hosts(True)
def test_ftp(tmpdir):
    tmpdir = str(tmpdir)
    dl = Downloader()

    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/warehouse/2011/2011_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/warehouse/2011/2013_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://notaserver/notafile.fileL", path=tmpdir)

    f = dl.download()
    assert len(f) == 1
    assert len(f.errors) == 3


@skip_windows
@pytest.mark.allow_hosts(True)
def test_ftp_http(tmpdir, httpserver):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T')
    dl = Downloader()

    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/warehouse/2011/2011_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/warehouse/2011/2013_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://ftp.swpc.noaa.gov/pub/_SRS.tar.gz", path=tmpdir)
    dl.enqueue_file("ftp://notaserver/notafile.fileL", path=tmpdir)
    dl.enqueue_file(httpserver.url, path=tmpdir)
    dl.enqueue_file("http://noaurl.notadomain/noafile", path=tmpdir)


    assert dl.queued_downloads == 6

    f = dl.download()
    assert len(f) == 2
    assert len(f.errors) == 4


def test_default_user_agent(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T',
                             headers={'Content-Disposition': "attachment; filename=testfile.fits"})

    dl = Downloader()
    dl.enqueue_file(httpserver.url, path=Path(tmpdir), max_splits=None)

    assert dl.queued_downloads == 1

    dl.download()

    assert 'User-Agent' in httpserver.requests[0].headers
    assert httpserver.requests[0].headers['User-Agent'] == f"parfive/{parfive.__version__} aiohttp/{aiohttp.__version__} python/{sys.version[:5]}"


def test_custom_user_agent(httpserver, tmpdir):
    tmpdir = str(tmpdir)
    httpserver.serve_content('SIMPLE  = T',
                             headers={'Content-Disposition': "attachment; filename=testfile.fits"})

    dl = Downloader(headers={'User-Agent': 'test value 299792458'})
    dl.enqueue_file(httpserver.url, path=Path(tmpdir), max_splits=None)

    assert dl.queued_downloads == 1

    dl.download()

    assert 'User-Agent' in httpserver.requests[0].headers
    assert httpserver.requests[0].headers['User-Agent'] == "test value 299792458"


@patch.dict(os.environ, {'HTTP_PROXY': "http_proxy_url",'HTTPS_PROXY': "https_proxy_url"})
@pytest.mark.parametrize("url,proxy",[('http://test.example.com','http_proxy_url'),('https://test.example.com','https_proxy_url')])
def test_proxy_passed_as_kwargs_to_get(tmpdir, url, proxy):

    with mock.patch(
                    "aiohttp.client.ClientSession._request",
                    new_callable=mock.MagicMock
                   ) as patched:

        dl = Downloader()
        dl.enqueue_file(url, path=Path(tmpdir), max_splits=None)

        assert dl.queued_downloads == 1

        dl.download()

    assert patched.called, "`ClientSession._request` not called"
    assert list(patched.call_args) == [('GET', url),
                                       {'allow_redirects': True,
                                        'timeout': ClientTimeout(total=300, connect=None, sock_read=90, sock_connect=None),
                                        'proxy': proxy
                                       }]
