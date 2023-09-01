# Cerasus

Cerasus is a simple dispatcher and quickstart function for ASGI apps based on Hypercorn
with Request/Response objects from Starlette.

The dispatcher uses a mapping like this:

``` python
{
    'GET': {
        '': webapp.index,
        '/': webapp.index,
        '/items/get': webapp.api.items.get
    },
    'POST': {
        '/items/add': webapp.api.items.add
    }
}
```

No REST. No Routes. What can be simpler?

The mapping is constructed by `expose` decorators. Here's an example that matches the mapping above:

``` python
from cerasus import expose, quickstart
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

class APIError(Exception):

    def __init__(self, status_code, description):
        self.status_code = status_code
        self.description = description

class Items:

    @expose
    async def get(self, request):
        items = []
        # get items
        # ...
        return JSONResponse(dict(status='success', items=items))

    @expose(methods='POST')
    async def add(self, request):
        item = await request.json()
        # add item
        # ...
        return JSONResponse(dict(status='success'))

class Api:
    # Customized error handling for API.
    # Error handlers can be either coroutines that return Starlette Response objects, or just objects:
    _undefined_method = JSONResponse(dict(status='error', description='Method not allowed'), status_code=405)
    _undefined_handler = JSONResponse(dict(status='error', description='Bad endpoint'), status_code=404)

    async def _exception_handler(self, request, exc):
        return JSONResponse(dict(status='error', description=exc.description), status_code=exc.status_code)

    # Note that children "inherit" error responses, i.e. we do not define them for Items
    items = Items()

class Webapp:
    api = Api()

    @expose(name='/')
    async def index(self, request):
        return HTMLResponse('<h1>Hello</h1>')

async def open_db():
    pass

async def close_db():
    pass

if __name__ == "__main__":
    quickstart(
        Api(),
        {
            'bind': ['127.0.0.1:12345']
            'alpn_protocols': ['h2']
        },
        base_urlpath = '/',
        on_startup = open_db,
        on_shutdown = close_db
    )
```
