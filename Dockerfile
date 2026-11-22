# The scorer builds this on linux/amd64 and runs it with no network. The
# digest is the image we labeled every commit in: its Python and SQLite
# decide which sqlglot tests run, so keep it if your image runs them.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 SKIP_INTEGRATION=0 PYTHONHASHSEED=0
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
# sqlglot at the pinned commit (GitHub archive of that exact sha), used as the pristine tree
COPY tools/fetch.py /tmp/fetch.py
RUN for i in 1 2 3 4 5; do python /tmp/fetch.py && break || sleep 10; done \
 && mv /tmp/x/sqlglot-* /opt/sqlglot && rm -rf /tmp/x /tmp/fetch.py \
 && python -m compileall -q /opt/sqlglot/sqlglot /opt/sqlglot/tests \
 && chmod -R a+rX /opt/sqlglot
COPY selector.py patchlib.py mapper.py runner.py cmpmap.py /app/
COPY data /app/data
RUN python -m compileall -q /app && chmod -R a+rX /app
CMD ["python", "/app/selector.py"]
