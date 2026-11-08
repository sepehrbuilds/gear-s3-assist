"""Small seekable MP3 prefix for pyatv's synchronous decoder callbacks.

The installed pyatv StreamReader wrapper loses read position while probing
formats. Keep our adapter local; do not patch the shared installed package.
Reads run in pyatv's executor and are fulfilled on the gateway event loop.
"""
import asyncio
import io


class MP3Stream(io.BufferedIOBase):
    def __init__(self, reader):
        self.reader = reader
        self.loop = asyncio.get_running_loop()
        self.head = bytearray()
        self.position = self.received = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_END:
            raise io.UnsupportedOperation('stream length is not known')
        target = self.position + offset if whence == io.SEEK_CUR else offset
        if not 0 <= target <= len(self.head):
            raise io.UnsupportedOperation('outside MP3 prefix')
        self.position = target
        return target

    def read(self, size=-1):
        if size < 0:
            raise io.UnsupportedOperation('unbounded stream read')
        if not size:
            return b''
        out = bytes(self.head[self.position:min(len(self.head), self.position+size)])
        self.position += len(out)
        size -= len(out)
        if size:
            if self.position != self.received:
                raise io.UnsupportedOperation('outside MP3 prefix')
            async def fetch():
                try:
                    return await self.reader.readexactly(size)
                except asyncio.IncompleteReadError as error:
                    return error.partial
            data = asyncio.run_coroutine_threadsafe(fetch(), self.loop).result()
            room = max(0, 65536 - len(self.head))
            self.head.extend(data[:room])
            self.received += len(data)
            self.position += len(data)
            out += data
        return out
