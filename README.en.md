# ComfyUI-EasyAPI

[简体中文](README.md) · English

Use EasyAPI for chat, images, video, audio, and real-time voice in ComfyUI. Each node lists models available to the selected connection and compatible with the selected operation. The form shows common controls alongside the parameters declared by the model.

## Installation

The extension requires Python 3.11 or later and ComfyUI's V3 node API. The tested baseline is ComfyUI 0.41.2 with frontend 1.55.17.

1. Place the `ComfyUI-EasyAPI` directory inside `ComfyUI/custom_nodes/`.
2. From the ComfyUI directory, install dependencies using the **Python interpreter that runs ComfyUI**.

   ```shell
   python -m pip install -r custom_nodes/ComfyUI-EasyAPI/requirements.txt
   ```

3. Restart ComfyUI, refresh the browser, and add nodes from the **EasyAPI** category.

The extension runs without local model weights. It uses ComfyUI's PyTorch, Pillow, and NumPy installations and ships with a ready-to-use frontend.

## Connections and models

Open **EasyAPI** from the left sidebar or the extension menu. With one connection, nodes use it automatically. With multiple connections, new nodes use the default connection when one is set. You can also select a connection from a node's context menu. New connections use `https://token.easyapi.com` as the default gateway.

- **Manual API key.** Enter an existing key for the official or a custom gateway. You can save the connection from ComfyUI over HTTP or HTTPS. The connection displays only the last characters of the saved key. Leave the field blank when editing to keep that key.
- **Website authorization.** Expand **Website authorization** at the bottom of the connection form and select **Authorize with EasyAPI**. The ComfyUI backend obtains a dedicated token for the instance, valid for 30 days. Renew it by authorizing again or revoke it from the EasyAPI console. This option requires trusted HTTPS or localhost.
- **Deployment credentials.** Set `EASYAPI_API_KEY_FILE` for the ComfyUI process to the path of a mounted secret file, or set `EASYAPI_API_KEY`. Set `EASYAPI_BASE_URL` to specify a gateway. The sidebar shows this connection as the read-only `easyapi-env` entry.

**Test saved connection** checks model access. The ComfyUI backend stores and uses the token obtained through website authorization.

Connection cards refresh their balances when you open the manager. You can also refresh each card using its icon. Where available, a card shows the wallet balance, API key quota totals and usage, and the configured budget. Older gateways expose key quota through `/usage/token`.

### Creative presets

Save named presets by connection, operation, and model. An operation's default preset supplies the model and parameters for new nodes. Existing workflows open with their saved models and values. From a node, you can apply a preset or save the current model and parameters as a new preset.

The model picker filters for operation-specific capabilities, including real-time chat, text-to-speech, and transcription. Model lists are cached by connection, operation, and language. The browser reuses them for up to two minutes, and the backend caches model permissions for five minutes. To reload the list, right-click the node and select **Refresh EasyAPI models**. If the list is empty, check the connection and key permissions in the sidebar.

Workflows store only connection IDs. The ComfyUI backend keeps keys in its persistent user directory. Users with access to the same ComfyUI instance can use its configured connections. Apply access controls at the deployment layer if you make the instance public.

## Available nodes

| Node | Purpose |
|---|---|
| EasyAPI Chat | Chat, Responses, Claude Messages, Gemini, and text completion |
| EasyAPI Images | Image generation, editing, and asynchronous image tasks |
| EasyAPI Video | Video generation through the unified `/v1/videos` endpoint |
| EasyAPI Speech | Text-to-speech with native `AUDIO` output |
| EasyAPI Transcribe | Transcription and translation for `AUDIO` or `VIDEO` with an audio track, using a compatible model |
| EasyAPI Count Tokens | Count tokens for compatible message requests |
| EasyAPI References | Group extra media, existing URLs, and reference roles |
| EasyAPI JSON | Parse, read, merge, set, append, and format JSON |
| EasyAPI Task Status / EasyAPI Wait for Task | Inspect or resume existing tasks without submitting another generation request |
| EasyAPI Download Results | Download and decode existing results |
| EasyAPI Realtime | Use voice or text in the browser and freeze a snapshot for the workflow |
| EasyAPI Preview | Preview text, JSON, images, audio, video, and media groups |

The extension registers 13 nodes. If an older workflow uses `EasyAPITools`, Moderation, Search, Rerank, Embedding, NativeRequest, Midjourney, or Suno, replace or remove those nodes. Advanced JSON accepts the parameters of the selected operation.

## Forms and languages

The node form shows a prompt and common settings, with dynamic parameters drawn from the selected model's schema. **Advanced JSON** starts collapsed and preserves values such as `0`, `false`, arrays, and objects. Both views edit the same parameters. Available inputs depend on the combination of connection, model, and operation.

The estimate beside the model reflects the current parameters. Click the price or info icon to see the rates, discounts, and estimate source. V1 billing displays USD and v2 expressions display CNY. On older gateways, supported catalog estimates serve as a reference. The final cost follows actual usage.

Node names, ports, and controls follow ComfyUI's language setting. The extension provides node definitions in English (`en`), Simplified Chinese (`zh`), and Traditional Chinese (`zh-TW`). Model IDs, operation values, reference roles, and JSON keys are the same across languages.

### Prompt editor and Wan tuning

The toolbar at the bottom right of the prompt field provides undo, redo, history, tags, translation, and prompt optimization. With a Wan 3 model selected, the tuning panel offers quick optimization, full analysis, and diagnosis of failed clips. It supports drama and e-commerce scenarios and covers text-to-video, first-frame, first-and-last-frame, multimodal reference, and video-editing workflows.

Tuning uses the current model parameters and available media and can analyze a selected time range in a generated clip. Media from an upstream node stays pending until that node has produced it. Text and media analysis models come from the selected connection, and calls are billed at the chosen model's normal rate. You can preview results before applying them to the prompt. Parameter suggestions appear separately. Run tuning again when the prompt, media, or generation context changes.

## Video and media

The video node uses the unified `/v1/videos` endpoint.

- `first_frame` and `last_frame` accept first and last frames as `IMAGE` inputs.
- `reference_image_1` and `reference_image_2` accept reference images.
- `reference_video_1` and `reference_video_2` accept reference videos.
- `reference_audio_1` and `reference_audio_2` accept reference audio.
- `references` accepts extra media grouped with the **EasyAPI References** node.

The `auto` input mode selects a mode from the connected media and the model's declared capabilities. The selected model determines supported ports, media counts, and combinations, including last-frame, first-and-last-frame, and multimodal references.

Local media goes directly to endpoints that accept files or inline data. When an endpoint requires a public URL, the plugin can upload media through `/v1/media`. Older gateways without that endpoint require an existing public URL for URL-only inputs. The default limit is 100 MiB per media item, and large files are streamed. [Interface coverage](docs/INTERFACE-COVERAGE.md)

## Tasks and real-time sessions

A generation node submits a new request each time it runs in the queue. For asynchronous operations, turn off **Wait for result** and connect the task output to a status, wait, or download node. Task IDs and polling details are stored in the persistent user directory so you can resume after stopping a wait, reaching a timeout, or restarting ComfyUI. The upstream task can continue after the local wait ends. If submission times out before returning a task ID, check the platform for the task before submitting it again.

Use the connection, voice or text, mute, interrupt, and end controls on the real-time node. Freeze or apply the conversation, then run the workflow to read the text and `AUDIO` snapshot. Live sessions run independently of the generation queue. The microphone starts when you activate it on the node.

Microphone access requires trusted HTTPS or localhost. The backend adds the key to the upstream connection. Ending the session, closing the page, or removing the node releases its resources.

## Examples and verification

The examples cover image editing, first-and-last-frame video, speech transcription, Claude Messages, video with reference audio, and separate task submission and polling. They use a connection named `mock`. Start the offline gateway and configure a connection with that name. [Example workflows](examples/README.md)

```shell
python tools/mock_gateway.py --port 3199
```

Use `http://127.0.0.1:3199` and the public test key `mock-only-key`. From Docker, use the mock gateway's address on the container network. The mock gateway serves local PNG, WAV, and MP4 fixtures for testing protocol and media handling offline.

```shell
python -m pip install -e '.[test]'
python -m pytest tests -q
```

The verification notes describe tests that used the mock gateway for generation, uploads, and real-time calls. See the deployment guide for environment setup and rollback. [Deployment guide](docs/DEPLOYMENT.md) · [Verification notes](docs/VERIFICATION.md)

MIT License · EasyAPI
