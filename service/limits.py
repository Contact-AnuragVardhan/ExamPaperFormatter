"""Cap streamed request bytes, including requests without Content-Length."""
from starlette.responses import JSONResponse

class BodyTooLarge(Exception): pass

class BodyLimit:
    def __init__(self,app,limit): self.app=app;self.limit=limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        total=0
        async def bounded():
            nonlocal total
            event=await receive()
            if event['type']=='http.request':
                total+=len(event.get('body',b''))
                if total>self.limit: raise BodyTooLarge()
            return event
        try: await self.app(scope,bounded,send)
        except BodyTooLarge:
            response=JSONResponse({'detail':'Request exceeds the upload limit.'},status_code=413)
            await response(scope,receive,send)
