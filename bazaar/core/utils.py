import hashlib
import json
import re
from django.utils import timezone
from datetime import timedelta
from tempfile import NamedTemporaryFile, TemporaryDirectory

import requests
from androguard.core.bytecodes import apk
from django.conf import settings
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils.html import escape
from django_q.models import Schedule
from django_q.tasks import schedule
from elasticsearch import Elasticsearch



def get_sha256_of_file_path(file_path):
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()


def get_sha256_of_file(f):
    sha256_hash = hashlib.sha256()
    for byte_block in iter(lambda: f.read(4096), b""):
        sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def is_secret(inp):
    inp = inp.lower()
    """Check if captures string is a possible secret."""
    iden = (
        'api"', 'key"', 'api_"', 'secret"',
        'password"', 'aws', 'gcp', 's3',
        'token"', 'username"', 'user_name"', 'user"',
    )
    not_string = (
        'label_', 'text', 'hint', 'msg_', 'create_',
        'message', 'new', 'confirm', 'activity_',
        'forgot', 'dashboard_', 'current_', 'signup',
        'sign_in', 'signin', 'title_', 'welcome_',
        'change_', 'this_', 'the_', 'placeholder',
        'invalid_', 'btn_', 'action_', 'prompt_',
        'lable', 'hide_', 'old', 'update', 'error',
        'empty', 'txt_', 'lbl_',
    )
    not_str = any(i in inp for i in not_string)
    return any(i in inp for i in iden) and not not_str


def url_n_email_extract(dat, relative_path):
    """Extract URLs and Emails from Source Code."""
    urls = []
    emails = []
    urllist = []
    url_n_file = []
    email_n_file = []
    # URLs Extraction My Custom regex
    pattern = re.compile(
        (
            r'((?:https?://|s?ftps?://|'
            r'file://|javascript:|data:|www\d{0,3}[.])'
            r'[\w().=/;,#:@?&~*+!$%\'{}-]+)'
        ),
        re.UNICODE)
    urllist = re.findall(pattern, dat)
    uflag = 0
    for url in urllist:
        if url not in urls:
            urls.append(url)
            uflag = 1
    if uflag == 1:
        url_n_file.append(
            {'urls': urls, 'path': escape(relative_path)})

    # Email Extraction Regex
    regex = re.compile(r'[\w.-]{1,20}@[\w-]{1,20}\.[\w]{2,10}')
    eflag = 0
    for email in regex.findall(dat.lower()):
        if (email not in emails) and (not email.startswith('//')):
            emails.append(email)
            eflag = 1
    if eflag == 1:
        email_n_file.append(
            {'emails': emails, 'path': escape(relative_path)})
    return urllist, url_n_file, email_n_file


def strings_from_apk(apk_file):
    """Extract the strings from an app."""
    try:
        print('Extracting Strings from APK')
        dat = []
        secrets = []
        and_a = apk.APK(apk_file)
        rsrc = and_a.get_android_resources()
        pkg = rsrc.get_packages_names()[0]
        rsrc.get_strings_resources()
        for i in rsrc.values[pkg].keys():
            res_string = rsrc.values[pkg][i].get('string')
            if res_string:
                for duo in res_string:
                    cap_str = '"' + duo[0] + '" : "' + duo[1] + '"'
                    if is_secret(duo[0] + '"'):
                        secrets.append(cap_str)
                    dat.append(cap_str)
        data_string = ''.join(dat)
        urls, urls_nf, emails_nf = url_n_email_extract(
            data_string, 'Android String Resource')
        return {
            'urls_list': list(set(urls)),
            'url_nf': urls_nf,
            'emails_nf': emails_nf,
            'secrets': secrets,
        }
    except Exception:
        print('Extracting Strings from APK')
        return {}


def upload_sample_to_malware_bazaar(sha256):
    es = Elasticsearch([settings.ELASTICSEARCH_HOST])
    try:
        result = es.get(index=settings.ELASTICSEARCH_APK_INDEX, id=sha256)['_source']
        if not result or 'vt' not in result:
            return

        if result['vt']['malicious'] > 1 and 'malware_bazaar' not in result:
            print(f'Upload {sha256}')
            headers = {'API-KEY': settings.MALWARE_BAZAAR_API_KEY}
            uri = reverse('front:report', args=[sha256])
            data = {
                'tags': [
                    'apk',
                ],
                'references': {
                    'links': [
                        f'https://beta.pithus.org{uri}',
                    ]
                }
            }
            print(data)
            with NamedTemporaryFile() as f:
                f.write(default_storage.open(sha256).read())
                f.seek(0)
                files = {
                    'json_data': (None, json.dumps(data), 'application/json'),
                    'file': (open(f.name, 'rb'))
                }
                response = requests.post('https://mb-api.abuse.ch/api/v1/', files=files, verify=False,
                                         headers=headers)
                if response.status_code < 400:
                    print(f'Update MB report in 15 minutes {sha256}')
                    schedule('bazaar.core.tasks.malware_bazaar_analysis', [sha256],
                             schedule_type=Schedule.ONCE,
                             next_run=timezone.now() + timedelta(minutes=30))

    except Exception:
        pass
