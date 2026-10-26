import os
import json
import sys
try:
    import requests
except Exception:
    print('requests not installed; please install with: python -m pip install requests')
    sys.exit(1)

DOWNLOAD = r"C:\Users\sunno\Downloads\YouTube Downloads"
try:
    jobs = requests.get('http://127.0.0.1:5000/jobs').json()
except Exception as e:
    print('Failed to fetch /jobs:', e)
    sys.exit(1)

for jid, job in jobs.items():
    fn = job.get('filename') or ''
    path = os.path.join(DOWNLOAD, fn)
    exists = os.path.exists(path)
    print('JOB', jid)
    print(' recorded filename:', fn)
    print(' exists on disk?:', exists)
    if not exists:
        print(' Similar files:')
        for f in os.listdir(DOWNLOAD):
            key = fn.split('.')[0].lower().replace('_','').replace(' ','')
            if key and key in f.lower().replace(' ','').replace('_',''):
                print('  ->', f)
        print(' Full listing:')
        for f in os.listdir(DOWNLOAD):
            print('  ', f)
    print()
