FROM python:3.7-slim

WORKDIR /opt

RUN apt-get update && \
    apt-get install -y git-core jq
COPY Pipfile Pipfile.lock ./
RUN pip install pipenv && \
    pipenv install && \
#    jq -r '.default | to_entries | .[] | select(.value.version != null) | .key + .value.version' Pipfile.lock > requirements.txt && \
#    pip install -r requirements.txt && \
    mkdir /data
COPY main.py authenticate.py ./

ENV STORAGE_DIR=/data DATABASE_FILE=/data/artifacts.sqlite AUTH_DIR=/data/auth

CMD ["pipenv", "run", "python", "./main.py" ]
