"""Image size extensions must agree with the form and the submitted request."""
import copy
import unittest

from easyapi_comfy.protocols import get_protocol
from test_realtime_nodes import load_nodes


class ImageParameterValidationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.nodes = load_nodes()
        self.definition = {
            "type": "string", "enum": ["auto", "1K", "512x512"],
            "x-size-spec": {"allow_custom": True, "multiple_of": 16,
                            "min_width": 256, "max_width": 2048,
                            "min_height": 256, "max_height": 2048,
                            "min_pixels": 262144, "max_pixels": 3145728,
                            "min_ratio": 0.5, "max_ratio": 2},
        }
        self.metadata = {"matched": True, "declared": True, "request_schema": {
            "type": "object", "required": ["size"], "properties": {
                "size": self.definition, "seed": {"type": "integer", "minimum": 0},
            },
        }}

    async def validate(self, body, metadata=None):
        await self.nodes.validate_request(None, "image-model", get_protocol("image-generation"),
                                          body, [], metadata=metadata or self.metadata)

    async def test_custom_size_is_normalized_without_mutating_metadata(self):
        original = copy.deepcopy(self.metadata)
        for size in ("1536x1024", "1536X1024", "1536*1024", "1536×1024"):
            with self.subTest(size=size):
                body = {"size": size, "seed": 0}
                await self.validate(body)
                self.assertEqual(body, {"size": "1536x1024", "seed": 0})
                self.assertEqual(self.metadata, original)

    async def test_enum_text_and_auto_are_preserved(self):
        for given, expected in (("AUTO", "auto"), ("1k", "1K"), ("512*512", "512x512")):
            with self.subTest(size=given):
                body = {"size": given}
                await self.validate(body)
                self.assertEqual(body["size"], expected)

    async def test_all_custom_size_constraints_are_enforced(self):
        for size in ("0x1024", "256.5x1024", "255x1024", "2064x1024", "1024x255",
                     "1024x2064", "513x1024", "256x256", "2048x2048", "256x1024",
                     "1024x256", 1024, "9007199254740992x1024"):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "size"):
                    await self.validate({"size": size})

    async def test_size_extension_does_not_relax_other_parameter_constraints(self):
        with self.assertRaisesRegex(ValueError, "seed"):
            await self.validate({"size": "1536x1024", "seed": -1})

    async def test_plain_size_fields_and_strict_enums_keep_existing_behavior(self):
        for definition, body in (({"type": "string"}, {"size": "arbitrary"}),
                                 ({"type": "string", "enum": ["1024x1024"]}, {"size": "1024x1024"})):
            metadata = copy.deepcopy(self.metadata)
            metadata["request_schema"]["properties"]["size"] = definition
            await self.validate(body, metadata)
        metadata = copy.deepcopy(self.metadata)
        metadata["request_schema"]["properties"]["size"]["x-size-spec"]["allow_custom"] = False
        with self.assertRaisesRegex(ValueError, "size"):
            await self.validate({"size": "1536x1024"}, metadata)

    async def test_auto_needs_an_explicit_declaration(self):
        metadata = copy.deepcopy(self.metadata)
        definition = metadata["request_schema"]["properties"]["size"]
        definition.pop("enum")
        with self.assertRaisesRegex(ValueError, "size"):
            await self.validate({"size": "auto"}, metadata)
        definition["default"] = "auto"
        body = {"size": "AUTO"}
        await self.validate(body, metadata)
        self.assertEqual(body["size"], "auto")


if __name__ == "__main__":
    unittest.main()
