import os
import click
import json
from oauth2client import client
from oauth2client import tools
from oauth2client.file import Storage
from pyicloud import PyiCloudService
import shutil
import tempfile


def prepare_auth_folder():
    folder = os.path.join(os.getcwd(), 'auth')
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    os.mkdir(folder)
    return folder


def authenticate_icloud(folder):
    username = click.prompt('Please enter your ICloud email').strip()
    password = click.prompt('Please enter your ICloud password').strip()
    tmp = os.path.join(tempfile.gettempdir(), 'photo-transfer-icloud')

    api = PyiCloudService(username, password, cookie_directory=folder)

    if api.requires_2sa:
        device = api.trusted_devices[0]
        phone = device.get('phoneNumber')
        if not api.send_verification_code(device):
            raise Exception(f'Failed to send verification code to {phone}')
        code = click.prompt(f'Please enter verification code sent to {phone}')
        if not api.validate_verification_code(device, code):
            raise Exception('Failed to verify verification code')

    with open(os.path.join(folder, 'icloud.json'), 'w') as file:
        file.write(f'{{"username":"{username}", "password":"{password}"}}')

    print('ICloud authentication succeeded')


def authenticate_gcloud(folder):
    client_secret_path = os.path.join(os.getcwd(), 'gcloud-client-secret.json')
    try:
        with open(client_secret_path) as client_secrets_file:
            client_info = json.load(client_secrets_file)
            if 'installed' in client_info:
                client_info = client_info['installed']
    except FileNotFoundError:
        raise Exception('Google authentication requires a gcloud-client-secret.json file, see README.md.')
    client_info['scope'] = 'https://www.googleapis.com/auth/photoslibrary'

    flow = client.OAuth2WebServerFlow(**client_info)
    storage = Storage(os.path.join(folder, 'gcloud.json'))
    credentials = tools.run_flow(flow, storage)


def main():
    try:
        folder = prepare_auth_folder()
        authenticate_icloud(folder)
        authenticate_gcloud(folder)
        print('Done')
    except Exception as e:
        print(f'Authentication failed: {str(e)}')


if __name__ == "__main__":
    main()
