"""Run offline workflows on the real ComfyUI instance, using only profile mock."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import ssl
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://192.168.2.48:3443')
    parser.add_argument('--ca', default='output/deployment/easyapi-comfy-lan-root.crt')
    parser.add_argument('--output', default='output/verification/comfy.json')
    parser.add_argument('--cases', default='', help='Comma-separated case names for a focused rerun')
    parser.add_argument('--write-examples', action='store_true')
    args = parser.parse_args()
    context = ssl.create_default_context(cafile=args.ca)
    def call(path, body=None):
        request = urllib.request.Request(args.url.rstrip('/') + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'} if body is not None else {})
        try:
            with urllib.request.urlopen(request, context=context, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f'{path}: HTTP {error.code}: {error.read().decode()[:3000]}') from error
    definitions = call('/object_info')
    removed = {'EasyAPIModeration', 'EasyAPISearch', 'EasyAPIRerank', 'EasyAPIEmbedding',
               'EasyAPINativeRequest', 'EasyAPIMidjourney', 'EasyAPISuno', 'EasyAPITools'}
    if removed.intersection(definitions):
        raise RuntimeError('The running instance still registers removed EasyAPI nodes.')
    profiles = call('/easyapi/profiles')['profiles']
    mock = next((p for p in profiles if p['id'] == 'mock'), None)
    if mock is None:
        raise RuntimeError('Create the offline mock connection before running verification. See docs/DEPLOYMENT.md.')
    if mock['base_url'] != 'http://comfytv-easyapi-mock:3199':
        raise RuntimeError('Refusing to generate: mock profile does not point to offline container')
    def node(kind, **values):
        inputs = {}
        def defaults(fields, prefix=''):
            for name, schema in fields.items():
                key = prefix + name
                options = schema[1] if len(schema) > 1 and isinstance(schema[1], dict) else {}
                if 'default' in options:
                    inputs[key] = options['default']
                elif isinstance(schema[0], list):
                    inputs[key] = schema[0][0]
                elif schema[0] == 'COMBO' and options.get('options'):
                    inputs[key] = options['options'][0]
                elif schema[0] == 'COMFY_DYNAMICCOMBO_V3' and options.get('options'):
                    branch = options['options'][0]
                    inputs[key] = branch['key']
                    defaults({**branch['inputs'].get('required', {}), **branch['inputs'].get('optional', {})}, key + '.')
        defaults({**definitions[kind]['input'].get('required', {}), **definitions[kind]['input'].get('optional', {})})
        inputs.update(values)
        return {'class_type': kind, 'inputs': inputs, '_meta': {'title': definitions[kind].get('display_name', kind)}}
    def creative(kind, operation, model, **values):
        fields = {'profile_id': 'mock', 'model': model, 'prompt': '离线接口验收', **values}
        if kind != 'EasyAPIImages':
            fields['operation'] = operation
        return node(kind, **fields)
    def preview(source, **values):
        return node('EasyAPIPreview', text=[source, 0], json_value=[source, 1], **values)
    cases = {
        '01-chat': {'1': creative('EasyAPIChat', 'chat', 'mock-text'), '2': preview('1')},
        '02-stream': {'1': creative('EasyAPIChat', 'chat', 'mock-text', params_json='{"stream":true,"temperature":0}'), '2': preview('1')},
        '03-image': {'1': creative('EasyAPIImages', 'image-generation', 'mock-image', params_json='{"seed":0,"watermark":false}'), '2': node('SaveImage', images=['1', 0], filename_prefix='EasyAPI-verify/image')},
        '04-image-edit': {'1': creative('EasyAPIImages', 'image-generation', 'mock-image'), '2': creative('EasyAPIImages', 'image-edit', 'mock-image', image=['1', 0]), '3': node('SaveImage', images=['2', 0], filename_prefix='EasyAPI-verify/edit')},
        '05-image-response': {'1': creative('EasyAPIImages', 'image-generation', 'mock-image'), '2': node('EasyAPIPreview', text=['1', 1], json_value=['1', 2]), '3': node('SaveImage', images=['1', 0], filename_prefix='EasyAPI-verify/image-response')},
        '06-video': {'1': creative('EasyAPIVideo', 'video-generation', 'mock-video'), '2': node('SaveVideo', video=['1', 0], filename_prefix='EasyAPI-verify/video')},
        '07-video-references': {'1': creative('EasyAPIImages', 'image-generation', 'mock-image'), '2': creative('EasyAPIVideo', 'video-generation', 'mock-video-frames', first_frame=['1', 0], last_frame=['1', 0], params_json='{"resolution":"480p"}'), '3': node('SaveVideo', video=['2', 0], filename_prefix='EasyAPI-verify/frames')},
        '08-speech-transcribe': {'1': creative('EasyAPISpeech', 'audio-speech', 'mock-audio'), '2': creative('EasyAPITranscribe', 'audio-transcriptions', 'mock-transcribe', audio=['1', 4]), '3': preview('2'), '4': node('SaveAudio', audio=['1', 4], filename_prefix='EasyAPI-verify/speech')},
        '10-claude-messages': {'1': creative('EasyAPIChat', 'messages', 'mock-text', params_json='{"max_tokens":16}'), '2': preview('1')},
        '11-video-audio-reference': {'1': creative('EasyAPISpeech', 'audio-speech', 'mock-audio'), '2': creative('EasyAPIVideo', 'video-generation', 'mock-video', reference_audio_1=['1', 4], params_json='{"generate_audio":false}'), '3': node('SaveVideo', video=['2', 0], filename_prefix='EasyAPI-verify/audio-reference')},
        '12-split-task': {'1': creative('EasyAPIVideo', 'video-generation', 'mock-video', wait_for_result=False), '2': node('EasyAPIPreview', json_value=['1', 2])},
        '13-json-tools': {'1': node('EasyAPIJSON', operation='parse', params_json='{"zero":0,"off":false,"nested":[1,2]}'), '2': node('EasyAPIPreview', text=['1', 1], json_value=['1', 0])},
    }
    output = Path(args.output)
    previous = json.loads(output.read_text(encoding='utf-8')).get('results', []) if args.cases and output.exists() else []
    results = []
    examples = Path('examples/api')
    if args.write_examples:
        examples.mkdir(parents=True, exist_ok=True)
    def run_graph(graph, started):
        queued = call('/prompt', {'prompt': graph, 'client_id': 'easyapi-verification-' + uuid.uuid4().hex})
        prompt_id = queued['prompt_id']
        while time.monotonic() - started < 100:
            histories = call('/history/' + prompt_id)
            if prompt_id in histories:
                history = histories[prompt_id]
                if history.get('status', {}).get('status_str') == 'error':
                    raise RuntimeError(json.dumps(history['status'], ensure_ascii=False)[:4000])
                return prompt_id, history
            time.sleep(.3)
        raise TimeoutError('Comfy workflow did not finish')
    for name, graph in cases.items():
        if args.cases and name not in args.cases.split(','):
            continue
        if args.write_examples:
            (examples / (name + '.json')).write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
        started = time.monotonic()
        try:
            prompt_id, history = run_graph(graph, started)
            if name == '12-split-task':
                handle = history.get('outputs', {}).get('1', {}).get('easyapi_task', [{}])[0]
                if not handle.get('task_id'):
                    raise RuntimeError('Submit-only video did not publish a recoverable task ID')
                resumed = {'1': node('EasyAPITaskWait', profile_id='mock', task_id=handle['task_id']),
                           '2': node('SaveVideo', video=['1', 5], filename_prefix='EasyAPI-verify/task-video')}
                prompt_id, history = run_graph(resumed, started)
            results.append({'case': name, 'ok': True, 'prompt_id': prompt_id, 'outputs': history.get('outputs', {}), 'elapsed': round(time.monotonic() - started, 3)})
            print(name, 'PASS', flush=True)
        except Exception as error:
            results.append({'case': name, 'ok': False, 'error': str(error), 'elapsed': round(time.monotonic() - started, 3)})
            print(name, 'FAIL', str(error)[:500], flush=True)
    results = [row for row in previous if row['case'] in cases and row['case'] not in {new['case'] for new in results}] + results
    results.sort(key=lambda row: row['case'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'url': args.url, 'nodes': [name for name in definitions if name.startswith('EasyAPI')], 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if all(row['ok'] for row in results) else 1)


if __name__ == '__main__':
    main()
