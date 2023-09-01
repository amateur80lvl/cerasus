'''
Simple quickstarter/dispatcher for ASGI apps based on Hypercorn
with Request/Response objects from Starlette.

:copyright: Copyright 2023 amateur80lvl
:license: LGPLv3, see LICENSE for details
'''

import asyncio
import logging
import traceback
import types

import hypercorn
import hypercorn.asyncio
from starlette.requests import Request
from starlette.responses import Response, HTMLResponse
import uvloop

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())


def expose(*args, **expose_params):
    '''
    The decorator is intended for use with class methods to
    mark them as 'exposed' with `expose_params`.

    expose_params:
        * name -- URL name or '/' for index page
        * method, methods  -- HTTP method, i.e. 'POST GET' or ['POST', 'GET']; default is 'GET'

    Exposed methods are collected by `build_request_handlers`.
    '''

    def prepare_expose_params():
        '''
        Sanitize some known `expose_params`.
        '''
        # set default methods to GET
        params = expose_params.copy()
        methods = set()
        for name in ['method', 'methods']:
            if name in params:
                value = params[name]
                if isinstance(value, str):
                    value = value.split()
                methods.update(m.upper() for m in value)
        if len(methods) == 0:
            methods.add('GET')
        params['methods'] = methods
        return params

    def decorator(func):

        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper.exposed = dict(**prepare_expose_params())
        return wrapper

    if len(args) and isinstance(args[0], types.FunctionType):
        return decorator(args[0])
    else:
        return decorator


def _build_request_handlers(root, base_url):
    '''
    Build request handlers from `root` object.
    The result is a dict where keys are URL paths and values are request handlers.

    Special member __objtree__ contains objects to look up error handlers.
    '''
    def process_exposed_handlers(obj, path, handlers, objtree_node):
        num_exposed = 0
        for attr_name in dir(obj):
            if attr_name.startswith('_'):
                continue

            value = getattr(obj, attr_name)
            if callable(value) and hasattr(value, 'exposed'):
                # process exposed handler
                handler_name = value.exposed.get('name', attr_name)
                for method in value.exposed['methods']:
                    num_exposed += 1
                    handlers.setdefault(method, dict())
                    if handler_name == '/':
                        # handler for index endpoint
                        handlers[method][path] = value
                        if path == '':
                            # duplicate for /
                            handlers[method]['/'] = value
                    else:
                        handlers[method][f'{path}/{handler_name}'] = value

            elif hasattr(obj, '__dict__'):
                child = dict(
                    obj = None,
                    children = dict()
                )
                if process_exposed_handlers(value, f'{path}/{attr_name}', handlers, child):
                    objtree_node['children'][attr_name] = child
                    objtree_node['obj'] = obj

        return num_exposed

    base_urlpath = base_url.rstrip('/')
    urlpath_parts = base_urlpath.split('/')

    handlers = dict()

    # init objtree with base_url
    objtree_root = dict(
        obj = None,
        children = dict()
    )
    handlers['__objtree__'] = objtree_root
    objtree_node = objtree_root
    for part in urlpath_parts[:-1]:
        child = dict(
            obj = None,
            children = dict()
        )
        objtree_node['children'][part] = child
        objtree_node = child
    objtree_node['obj'] = root

    # collect exposed handlers
    process_exposed_handlers(root, base_urlpath, handlers, objtree_node)

    # XXX debugging
    from pprint import pprint
    pprint(handlers)
    return handlers


async def _dispatch(scope, handlers, receive, send):
    '''
    Dispatch ASGI request.
    '''
    request = Request(scope, receive)

    # get handlers for HTTP method
    try:
        path_handlers = handlers[request.method]
    except KeyError:
        response = await _undefined_method(request, handlers)
        await response(scope, receive, send)
        return

    # get request handler
    try:
        handler = path_handlers[request['path']]
    except KeyError:
        response = await _undefined_endpoint(request, handlers)
        await response(scope, receive, send)
        return

    # call request handler
    try:
        response = await handler(request)
    except Exception as exc:
        logger.debug(traceback.format_exc())
        response = await _run_exception_handler(exc, request, handlers)

    await response(scope, receive, send)


async def _undefined_method(request, handlers):
    '''
    Find and run error handler for undefined method.
    '''
    handler = _find_error_handler('_undefined_method', request['path'], handlers)
    if handler is not None:
        if isinstance(handler, Response):
            return handler
        else:
            return await handler(request)

    # default handler
    return HTMLResponse('''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Method Not Allowed</title>
</head>
<body>
    <h1>Error 405: Method Not Allowed</h1>
    <p>
    The request method is known by the server but is not supported by the target resource.
    </p>
</body>
</html>
''', status_code=405)


async def _undefined_endpoint(request, handlers):
    '''
    Find and run error handler for undefined endpoint.
    '''
    handler = _find_error_handler('_undefined_endpoint', request['path'], handlers)
    if handler is not None:
        if isinstance(handler, Response):
            return handler
        else:
            return await handler(request)

    # default handler
    return HTMLResponse('''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Not Found</title>
</head>
<body>
    <h1>Error 404: Not Found</h1>
    <p>
    The server cannot find the requested resource.
    </p>
</body>
</html>
''', status_code=404)


async def _run_exception_handler(exc, request, handlers):
    '''
    Find and run general error handler.
    '''
    handler = _find_error_handler('_exception_handler', request['path'], handlers)
    if handler is not None:
        if isinstance(handler, Response):
            return handler
        else:
            return await handler(request, exc)

    # default handler
    return HTMLResponse(f'''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Internal Server Error</title>
</head>
<body>
    <h1>Error 500: Internal Server Error</h1>
    <p>
    The following error has occured during procesing the request:
    </p>
    <p>
    {str(exc)}
    </p>
</body>
</html>
''', status_code=500)


def _find_error_handler(handler_name, urlpath, handlers):
    '''
    Try to find the requested error handler for urlpath.
    '''
    def find_handler(urlpath, objtree):
        if urlpath[0] not in objtree['children']:
            return getattr(objtree['obj'], handler_name, None)
        handler = find_handler(urlpath[1:], objtree['children'][urlpath[0]])
        if handler is None:
            return getattr(objtree['obj'], handler_name, None)
        else:
            return handler

    urlpath = urlpath.rstrip('/').split('/')
    return find_handler(urlpath, handlers['__objtree__'])


def quickstart(root, config={}, base_urlpath='/', on_startup=None, on_shutdown=None):
    '''
    Create and run web application.
    '''
    handlers = _build_request_handlers(root, base_urlpath)
    hconf = hypercorn.config.Config.from_mapping(config)

    async def main():

        async def app(scope, receive, send):
            '''
            ASGI application based on Starlette Request and responses.
            '''
            if scope['type'] == 'lifespan':
                while True:
                    message = await receive()

                    if message['type'] == 'lifespan.startup':
                        if on_startup:
                            try:
                                await on_startup()
                            except Exception:
                                logger.error(traceback.format_exc())
                        await send({'type': 'lifespan.startup.complete'})

                    elif message['type'] == 'lifespan.shutdown':
                        if on_shutdown:
                            try:
                                await on_shutdown()
                            except Exception:
                                logger.error(traceback.format_exc())
                        await send({'type': 'lifespan.shutdown.complete'})
                        return

            await _dispatch(scope, handlers, receive, send)

        await hypercorn.asyncio.serve(app, hconf)

    uvloop.install()
    asyncio.run(main())
