import json, time, subprocess, urllib.request
from autocut.editor import _duration as FF

SAMPLE = r'C:/Users/Flame/Downloads/ssstik.io_@beersaddam_1780837621177.mp4'


def post(fields):
    args = ['curl', '-s', '-X', 'POST', 'http://localhost:5000/process',
            '-F', 'videos=@' + SAMPLE]
    for k, v in fields.items():
        args += ['-F', k + '=' + v]
    return json.loads(subprocess.run(args, capture_output=True, text=True).stdout)['job_id']


def wait(j):
    while True:
        s = json.load(urllib.request.urlopen('http://localhost:5000/status/' + j))
        if s['status'] in ('done', 'error'):
            return s
        time.sleep(3)


def run(tag, fields, out):
    j = post(fields)
    s = wait(j)
    status = s['status']
    sel = [l for l in s['logs'] if ('เลือก' in l) or ('dead air' in l)]
    urllib.request.urlretrieve('http://localhost:5000/result/' + j, out)
    dur = FF(out)
    print('\n== ' + tag + ' ==  status=' + status)
    for l in sel:
        print('   ' + l)
    print('   merged duration = %.2fs' % dur)


base = dict(output_mode='merged', output_format='mp4', max_duration='90', lj_cut_on='false')
run('dead-air OFF', dict(base, dead_air_on='false'), '_verify/seq_off.mp4')
run('dead-air STRONG', dict(base, dead_air_on='true', dead_air_aggr='strong'), '_verify/seq_strong.mp4')
