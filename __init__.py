"""EasyAPI custom-node pack: one V3 entrypoint, optional browser extension."""
WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension
    from .easyapi_comfy.nodes import NODE_CLASSES
    from .easyapi_comfy.server import register_routes
    from .easyapi_comfy.realtime import register_routes as register_realtime_routes

    class EasyAPIExtension(ComfyExtension):
        async def on_load(self):
            register_routes()
            register_realtime_routes()

        async def get_node_list(self):
            return NODE_CLASSES

    return EasyAPIExtension()


__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
