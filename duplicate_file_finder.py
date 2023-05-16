import dropbox
import pathlib
import json
from dropbox.exceptions import AuthError

APP_KEY = "{enter app key here}"

def dropbox_login():
    """Create a connection to Dropbox."""

    try:
        auth_flow = dropbox.DropboxOAuth2FlowNoRedirect(APP_KEY, use_pkce=True, token_access_type='offline')
        authorize_url = auth_flow.start()
        print("1. Go to: " + authorize_url)
        print("2. Click \"Allow\" (you might have to log in first).")
        print("3. Copy the authorization code.")
        auth_code = input("Enter the authorization code here: ").strip()
        oauth_result = auth_flow.finish(auth_code)
        return oauth_result.refresh_token
    except AuthError as e:
        print('Error connecting to Dropbox with access token: ' + str(e))
        raise e


def dropbox_content_hash(dbx: dropbox.Dropbox, path):
    try:
        metadata = dbx.files_get_metadata(path)
        return metadata.content_hash
    except Exception as ex:
        print(f"Exeption trying to find data for {path}: {ex}")
    
root_dropbox_local_folder = "{root dropbox folder path here}"
local_file_path = f"{root_dropbox_local_folder}/media/"
media = pathlib.Path(local_file_path)

image_files = [str(file).replace(root_dropbox_local_folder, "") for file in media.rglob("*") if file.is_file()]
refresh_token = dropbox_login()
count = 0
hashes = {}

with dropbox.Dropbox(oauth2_refresh_token=refresh_token, app_key=APP_KEY) as dbx:
    for file in image_files:
        content_hash = dropbox_content_hash(dbx, file)
        if content_hash:
            count += 1
            hash_file_list = hashes.get(content_hash, {"count": 0, "files": []})
            hash_file_list["files"].append(file)
            hash_file_list["count"] += 1
            entry = {content_hash: hash_file_list}
            hashes.update(entry)
            
with open("initial_list.txt", "w") as file_list:
    file_list.write(json.dumps(hashes))
