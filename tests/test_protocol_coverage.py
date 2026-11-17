"""Every declared operation must have an executable offline protocol fixture."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp.test_utils import TestServer
from easyapi_comfy.client import EasyAPIClient, UploadFile
from easyapi_comfy.protocols import PROTOCOLS
from easyapi_comfy.tasks import TaskStore, create_task_handle, wait_for_task, TaskTimeout
from tools.mock_gateway import create_app, PNG, audio_bytes


class ProtocolCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app()
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.client = EasyAPIClient(base_url=str(self.server.make_url('')).rstrip('/'), api_key='mock-only-key')
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        (self.path / 'image.png').write_bytes(PNG)
        (self.path / 'audio.wav').write_bytes(audio_bytes())

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()
        self.directory.cleanup()

    async def test_each_declared_http_operation_has_fixture(self):
        exercised = []
        for operation in PROTOCOLS:
            if operation['transport'] == 'websocket':
                continue
            with self.subTest(operation=operation['id']):
                family = {'image': 'image', 'midjourney': 'mj', 'suno': 'music'}.get(operation['family'], 'video')
                task_id = 'fixture_' + family
                self.app['jobs'][task_id] = {'family': family, 'polls': 0, 'fail': False, 'body': {}, 'files': []}
                path = operation['path'].format(task_id=task_id, model='mock-text', action='generate', media_id='fixture_media', id=task_id, mode='fast')
                if operation['id'] == 'custom-user-request':
                    path = '/v1/chat/completions'
                self.app['assets']['fixture_media'] = {'id': 'fixture_media', 'url': str(self.server.make_url('/fixtures/image.png'))}
                body = {'model': 'mock-text', 'prompt': 'offline', 'input': 'offline', 'query': 'offline', 'messages': [], 'ids': [task_id]}
                files = None
                if operation['transport'] == 'multipart':
                    audio = 'audio' in operation['id'] and operation['id'] != 'audio-speech'
                    field = 'file' if audio or operation['id'] == 'media-upload' else 'image'
                    name = 'audio.wav' if audio else 'image.png'
                    files = [UploadFile(field, self.path / name)]
                if operation['method'] == 'GET':
                    body = None
                response = await self.client.request(operation['method'], path, body=body, files=files, query=operation.get('query'))
                self.assertLess(response.status, 300)
                self.assertTrue(response.data is not None or response.content)
                exercised.append(operation['id'])
        self.assertEqual(len(exercised), sum(op['transport'] != 'websocket' for op in PROTOCOLS))

    async def test_sse_preserves_split_utf8_and_events(self):
        response = await self.client.request('POST', '/v1/chat/completions', body={'stream': True}, stream=True)
        events = response.data['events']
        text = ''.join(e['data'].get('choices', [{}])[0].get('delta', {}).get('content', '') for e in events)
        self.assertEqual(text, '离线验证成功')
        self.assertEqual(events[-1]['data']['usage']['completion_tokens'], 3)

    async def test_real_multipart_preserves_file_and_false_zero_nested_values(self):
        response = await self.client.request('POST', '/v1/images/edits',
            body={'model': 'mock-image', 'seed': 0, 'watermark': False, 'metadata': {'roles': ['first_frame']}},
            files=[UploadFile('image', self.path / 'image.png'), UploadFile('mask', self.path / 'image.png')])
        entry = next(item for item in reversed(self.app['ledger']) if item['path'] == '/v1/images/edits')
        self.assertEqual(entry['body']['seed'], 0)
        self.assertIs(entry['body']['watermark'], False)
        self.assertEqual(entry['body']['metadata'], {'roles': ['first_frame']})
        self.assertEqual([f['field'] for f in entry['files']], ['image', 'mask'])
        self.assertTrue(all(f['size'] == len(PNG) for f in entry['files']))
        self.assertTrue(response.data['data'][0]['url'])

    async def test_wait_resumes_persisted_handle_without_resubmission(self):
        operation = next(op for op in PROTOCOLS if op['id'] == 'video-generation')
        response = await self.client.request('POST', '/v1/videos', body={'model': 'mock-video', 'prompt': 'offline'})
        store = TaskStore(self.path / 'tasks.json')
        handle = store.save(create_task_handle(response, 'mock', operation))
        with self.assertRaises(TaskTimeout):
            await wait_for_task(self.client, store, handle, timeout=0, interval=.01)
        reopened = TaskStore(self.path / 'tasks.json')
        result = await wait_for_task(self.client, reopened, reopened.get(handle['task_id']), timeout=2, interval=.01)
        self.assertEqual(result.data['data']['status'], 'completed')
        self.assertEqual(sum(item['method'] == 'POST' and item['path'] == '/v1/videos' for item in self.app['ledger']), 1)

    async def test_realtime_mock_duplex_pcm(self):
        session = await self.client.session()
        async with session.ws_connect(self.server.make_url('/v1/realtime'), headers=self.client.headers()) as ws:
            self.assertEqual((await ws.receive_json())['type'], 'session.created')
            await ws.send_json({'type': 'input_audio_buffer.commit'})
            kinds = []
            while True:
                event = await asyncio.wait_for(ws.receive_json(), 2)
                kinds.append(event['type'])
                if event['type'] == 'response.done':
                    break
            self.assertIn('response.audio.delta', kinds)
            self.assertIn('conversation.item.input_audio_transcription.completed', kinds)


if __name__ == '__main__':
    unittest.main()
