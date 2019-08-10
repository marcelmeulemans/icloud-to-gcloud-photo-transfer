FROM python:3.7-slim

WORKDIR /opt

COPY Pipfile Pipfile.lock ./
RUN apt-get update && apt-get install -y git-core && pip install --no-cache-dir pipenv && pipenv install && mkdir /data
COPY main.py ./

ENV STORAGE_DIR=/data DATABASE_FILE=/data/artifacts.sqlite

CMD ["pipenv", "run", "python", "./main.py" ]
