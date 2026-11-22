import io, tarfile, urllib.request
URL = "https://github.com/tobymao/sqlglot/archive/eb11aec4be8cb2122afe713a4816662c3f0fb2fa.tar.gz"
tarfile.open(fileobj=io.BytesIO(urllib.request.urlopen(URL, timeout=60).read())).extractall("/tmp/x")
