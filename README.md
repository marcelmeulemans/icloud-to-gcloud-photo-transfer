# Transfer all your photos from ICloud to Google photos.

This project helps transfer your photos from ICloud to Google photos. Both cloud providers do not seem to have a service that helps you do this so the only way is to download your photos from ICloud and upload them to Google. Obviously this is tedious and error prone work which should be automated so this script does it for you.

## Usage

The idea is to build the docker container and run it in a google compute instance (because you probably don't want to waste your home bandwidth downloading and uploading all your photos again):

```
docker build --tag photo-transfer .
```

The script needs some persistent storage for its administration database which you really want to store outside of the container (so you can kill the container if you want).

```
docker run --name photo-transfer -e AUTH_DIR=/auth -e STORAGE_DIR=/data -v ${PWD}/auth:/auth -v /home/me/photo-transfer:/data photo-transfer
```

All your photos will be uploaded to the google photos account related to the account you logged in with. Additionally your photos will be put in a new album named "From ICloud" so you can review them.

## Authentication

The script needs access to your ICloud and Google account. Run the authentiation.py from a console to generate the required credentials (in the `auth` folder) and mount this auth folder into your container.

## Design

The script is designed to be safe so that you can be sure all your photos have been transferred. The script uses some persistent storage to keep track of what it is doing. This means you may kill the script at any time and it will continue where it left off. It will even transfer photos that were uploaded to ICloud while the script was running. Concretely the script defines a number of workers that run in parallel and perform one specific task. The workers communicate via an sqlite database.

```
docker build . -t photos
docker run -ti -v /home/mmeulemans/icloud-to-gcloud-photo-transfer/gcloud-client-secret.json:/opt/gcloud-client-secret.json -v /home/mmeulemans/storage:/data -w /opt photos pipenv run python /opt/authenticate.py
```

