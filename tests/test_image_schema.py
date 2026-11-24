"""Image forms use declared parameters without losing execution metadata."""
import copy
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from easyapi_comfy.client import EasyAPIClient


def endpoint(operation, schema):
    return {"key": operation, "path": "/v1/images/edits" if operation == "image-edit" else "/v1/images/generations",
            "method": "POST", "transport": "multipart" if operation == "image-edit" else "json",
            "request_schema": schema}


class ImageSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.docs, self.schemas, self.details = {}, {}, {}
        app = web.Application()

        async def metadata(request):
            source = {"/pricing/docs": self.docs, "/pricing/schema": self.schemas,
                      "/pricing/detail": self.details}[request.path]
            data = source.get((request.query.get("model_name"), request.query.get("endpoint")))
            if data is None:
                data = source.get(request.query.get("model_name"))
            if data is None:
                return web.json_response({"success": False}, status=404)
            return web.json_response({"success": True, "data": copy.deepcopy(data)})

        for path in ("/pricing/docs", "/pricing/schema", "/pricing/detail"):
            app.router.add_get(path, metadata)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = EasyAPIClient(str(self.server.make_url("")), "offline-image-key")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_transport_only_docs_continue_to_endpoint_schema_and_preserve_upload_contract(self):
        self.docs["edit"] = {"category": "image", "endpoints": [endpoint("image-edit", {
            "type": "object", "properties": {"image": {"type": "string", "format": "binary"},
                                               "prompt": {"type": "string"}}, "required": ["image"]})]}
        self.schemas[("edit", "image-edit")] = {"endpoints": [endpoint("image-edit", {})], "request": {
            "type": "object", "properties": {"size": {"type": "string", "enum": ["1024x1024"]},
                                               "quality": {"type": "string", "default": "high"}},
            "required": ["quality"]}}
        result = await self.client.model_schema("edit", "image-edit")
        self.assertEqual(result["source"], "schema")
        self.assertEqual(result["request_schema"]["properties"]["image"]["format"], "binary")
        self.assertCountEqual(result["request_schema"]["required"], ["image", "quality"])
        self.assertEqual(set(result["form_schema"]["properties"]), {"size", "quality"})
        self.assertEqual(result["form_schema"]["required"], ["quality"])
        self.assertIsNone(result["fallback"])

    async def test_endpoint_required_fields_survive_parameter_fallback(self):
        edit = endpoint("image-edit", {"type": "object", "properties": {
            "image": {"type": "string", "format": "binary"}}})
        edit["required"] = ["image"]
        self.docs["required"] = {"category": "image", "endpoints": [edit]}
        self.schemas["required"] = {"request": {"type": "object", "properties": {"n": {"type": "integer"}}}}
        result = await self.client.model_schema("required", "image-edit")
        self.assertEqual(result["request_schema"]["required"], ["image"])
        self.assertEqual(result["form_schema"]["required"], [])

    async def test_managed_only_docs_and_schema_continue_to_endpoint_parameter_overrides(self):
        managed = {"type": "object", "properties": {"model": {"type": "string"},
                   "prompt": {"type": "string"}, "stream": {"type": "boolean"},
                   "async": {"type": "boolean"}, "async_mode": {"type": "boolean"}}}
        edit = copy.deepcopy(managed)
        edit["properties"]["image"] = {"type": "string", "format": "binary"}
        edit["required"] = ["image"]
        self.docs["both"] = {"category": "image", "endpoints": [endpoint("image-generation", managed), endpoint("image-edit", edit)]}
        self.schemas["both"] = {"request": {"type": "object", "properties": {"stream": {"type": "boolean"}}}}
        self.details["both"] = {"category": "Image", "params": {"image": {
            "properties": {"prompt": {"type": "string", "required": True},
                           "size": {"type": "string", "enum": ["1024x1024"]}},
            "endpoint_params": {"image-edit": {"properties": {
                "size": {"type": "string", "enum": ["512x512"]},
                "strength": {"type": "number", "min": 0, "max": 1, "required": True}}}}}}}
        generated = await self.client.model_schema("both", "image-generation")
        edited = await self.client.model_schema("both", "image-edit")
        self.assertEqual(generated["source"], "detail")
        self.assertEqual(edited["source"], "detail")
        self.assertEqual(generated["form_schema"]["properties"]["size"]["enum"], ["1024x1024"])
        self.assertEqual(set(generated["form_schema"]["properties"]), {"size"})
        self.assertEqual(edited["form_schema"]["properties"]["size"]["enum"], ["512x512"])
        self.assertEqual(edited["form_schema"]["properties"]["strength"]["minimum"], 0)
        self.assertEqual(edited["form_schema"]["required"], ["strength"])
        self.assertEqual(edited["request_schema"]["properties"]["image"]["format"], "binary")
        self.assertCountEqual(edited["request_schema"]["required"], ["image", "prompt", "strength"])

    async def test_complete_docs_keep_priority_and_normalize_size_specs_without_cache_aliases(self):
        self.docs["custom"] = {"category": "image", "endpoints": [endpoint("image-generation", {
            "type": "object", "properties": {
                "size": {"type": "string", "size_spec": {"allow_custom": True, "min_pixels": 262144}},
                "settings": {"type": "object", "properties": {
                    "sizes": {"type": "array", "items": {"type": "string", "size_spec": {"multiple_of": 16}}}}},
                "output_size": {"type": "string", "size_spec": {"multiple_of": 64},
                                "x-size-spec": {"multiple_of": 32}}}})]}
        self.schemas["custom"] = {"request": {"properties": {"wrong": {"type": "string"}}}}
        self.details["custom"] = {"category": "image", "params": {"image": {"properties": {"wrong": {"type": "string"}}}}}
        result = await self.client.model_schema("custom", "image-generation")
        self.assertEqual(result["source"], "docs")
        self.assertEqual(set(result["form_schema"]["properties"]), {"size", "settings", "output_size"})
        props = result["form_schema"]["properties"]
        self.assertEqual(props["size"]["x-size-spec"], {"allow_custom": True, "min_pixels": 262144})
        self.assertEqual(props["settings"]["properties"]["sizes"]["items"]["x-size-spec"], {"multiple_of": 16})
        self.assertEqual(props["output_size"]["x-size-spec"], {"multiple_of": 32})
        props["size"]["x-size-spec"]["min_pixels"] = 1
        self.assertEqual(result["request_schema"]["properties"]["size"]["x-size-spec"]["min_pixels"], 262144)
        cached = await self.client.model_schema("custom", "image-generation")
        self.assertEqual(cached["form_schema"]["properties"]["size"]["x-size-spec"]["min_pixels"], 262144)
        self.assertNotIn("x-size-spec", self.docs["custom"]["endpoints"][0]["request_schema"]["properties"]["size"])

    async def test_missing_parameter_declarations_expose_only_advanced_json(self):
        self.docs["transport"] = {"category": "image", "endpoints": [endpoint("image-edit", {
            "type": "object", "properties": {"image": {"type": "string", "format": "binary"}},
            "required": ["image"]})]}
        transport = await self.client.model_schema("transport", "image-edit")
        self.assertTrue(transport["declared"])
        self.assertTrue(transport["matched"])
        self.assertEqual(transport["request_schema"]["required"], ["image"])
        self.assertEqual(transport["form_schema"]["properties"], {})
        self.assertEqual(transport["fallback"], "advanced-json")
        missing = await self.client.model_schema("missing", "image-generation")
        self.assertFalse(missing["declared"])
        self.assertFalse(missing["matched"])
        self.assertEqual(missing["form_schema"]["properties"], {})
        self.assertEqual(missing["fallback"], "advanced-json")
        text = await self.client.model_schema("missing", "chat")
        self.assertEqual(text["fallback"], "basic-and-advanced-json")
        self.assertIn("temperature", text["form_schema"]["properties"])

    async def test_incompatible_endpoint_metadata_never_becomes_image_parameters(self):
        schema = {"type": "object", "properties": {"quality": {"type": "string"}}}
        self.docs["docs-mismatch"] = {"category": "image", "endpoints": [endpoint("image-generation", schema)]}
        self.schemas["docs-mismatch"] = {"request": schema}
        self.schemas["schema-mismatch"] = {"request": schema, "endpoints": [endpoint("image-generation", schema)]}
        self.details["schema-mismatch"] = {"category": "image", "params": {"image": {"properties": schema["properties"]}}}
        self.details["detail-mismatch"] = {"category": "image", "endpoints": [endpoint("image-edit", schema)],
                                           "params": {"image": {"properties": schema["properties"]}}}
        for model, operation in (("docs-mismatch", "image-edit"), ("schema-mismatch", "image-edit"),
                                 ("detail-mismatch", "image-generation")):
            with self.subTest(model=model):
                result = await self.client.model_schema(model, operation)
                self.assertFalse(result["matched"])
                self.assertEqual(result["form_schema"]["properties"], {})
                self.assertEqual(result["fallback"], "advanced-json")

    async def test_image_forms_hide_credentials_media_and_forbidden_route_controls(self):
        hidden = {name: {"type": "string"} for name in (
            "model", "prompt", "api_key", "apiKey", "Authorization", "token", "access_token", "image", "images",
            "image_url", "mask", "references", "input_references", "audio", "video", "file", "files",
            "stream", "async", "async_mode", "__path", "__method", "__transport", "__polling")}
        self.docs["private"] = {"category": "image", "endpoints": [endpoint("image-generation", {
            "type": "object", "properties": {**hidden, "seed": {"type": "integer", "default": 0},
                "watermark": {"type": "boolean", "default": False}, "upload": {"type": "string", "format": "binary"},
                "settings": {"type": "object", "properties": {"api_key": {"type": "string"},
                    "strength": {"type": "number"}}, "required": ["api_key", "strength"]}},
            "required": ["model", "api_key", "seed"]})]}
        result = await self.client.model_schema("private", "image-generation")
        self.assertEqual(set(result["form_schema"]["properties"]), {"seed", "watermark", "settings"})
        self.assertEqual(result["form_schema"]["required"], ["seed"])
        settings = result["form_schema"]["properties"]["settings"]
        self.assertEqual(set(settings["properties"]), {"strength"})
        self.assertEqual(settings["required"], ["strength"])
        self.assertIn("api_key", result["request_schema"]["properties"])
        self.assertEqual(result["form_schema"]["properties"]["seed"]["default"], 0)
        self.assertIs(result["form_schema"]["properties"]["watermark"]["default"], False)

    async def test_nested_business_fields_are_not_treated_as_top_level_node_inputs(self):
        self.docs["nested"] = {"category": "image", "endpoints": [endpoint("image-generation", {
            "type": "object", "properties": {
                "model": {"type": "string"}, "prompt": {"type": "string"}, "stream": {"type": "boolean"},
                "settings": {"type": "object", "properties": {
                    "prompt": {"type": "string"}, "model": {"type": "string"}, "content": {"type": "string"},
                    "input": {"type": "string"}, "stream": {"type": "boolean"},
                    "api_key": {"type": "string"}, "Authorization": {"type": "string"},
                    "__path": {"type": "string"}, "upload": {"type": "string", "format": "binary"}},
                    "required": ["prompt", "model", "api_key"]}}})]}
        result = await self.client.model_schema("nested", "image-generation")
        self.assertEqual(set(result["form_schema"]["properties"]), {"settings"})
        settings = result["form_schema"]["properties"]["settings"]
        self.assertEqual(set(settings["properties"]), {"prompt", "model", "content", "input", "stream"})
        self.assertEqual(settings["required"], ["prompt", "model"])
        self.assertIn("api_key", result["request_schema"]["properties"]["settings"]["properties"])


if __name__ == "__main__":
    unittest.main()
