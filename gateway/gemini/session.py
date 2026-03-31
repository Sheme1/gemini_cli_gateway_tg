import asyncio
import logging
from typing import Callable, Optional

from gateway.config import Config
from gateway.gemini.parser import GeminiStreamParser

logger = logging.getLogger(__name__)


class SessionManager:
    """Управляет одним постоянным процессом Gemini CLI для сохранения контекста."""

    def __init__(self, config: Config):
        self.config = config
        self.process: Optional[asyncio.subprocess.Process] = None
        self.lock = asyncio.Lock()
        self._master_fd: Optional[int] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def spawn(self, resume: bool = False) -> None:
        """Запуск процесса gemini в interactive backend mode."""
        import os
        import pty
        args = [
            "gemini",
            "-m",
            self.config.gemini_model,
            "-o",
            "stream-json",
        ]
        args.extend(self.config.approval_mode_flag)
        args.extend(self.config.sandbox_flag)

        if resume:
            args.extend(["--resume", "latest"])

        logger.info(f"Spawning Gemini CLI: {' '.join(args)}")

        master_fd, slave_fd = pty.openpty()

        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=self.config.gemini_working_dir,
            preexec_fn=os.setsid
        )

        os.close(slave_fd)

        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, os.fdopen(master_fd, 'rb', buffering=0))

        transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin,
            os.fdopen(os.dup(master_fd), 'wb', buffering=0)
        )
        writer = asyncio.StreamWriter(transport, w_protocol, reader, loop)

        self._master_fd = master_fd
        self._reader = reader
        self._writer = writer

    async def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def kill(self) -> None:
        """Завершить текущий процесс."""
        if await self.is_alive():
            logger.info("Terminating current gemini process...")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Process did not terminate in time. Killing it.")
                self.process.kill()
                await self.process.wait()
                
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._reader = None
        self._master_fd = None
        self.process = None

    async def reset(self) -> None:
        """Сброс контекста (/new): убить процесс и запустить новый."""
        async with self.lock:
            await self.kill()
            await self.spawn(resume=False)

    async def send_prompt(
        self,
        prompt: str,
        on_chunk: Callable[[str], asyncio.Future],
        on_approval: Callable[[dict], asyncio.Future],
    ) -> None:
        """Отправить промпт в процесс и стримить ответ через обратные вызовы."""
        async with self.lock:
            if not await self.is_alive():
                logger.info("Process is dead or not started. Spawning new one.")
                await self.spawn()

            logger.info(f"Sending prompt to process: {prompt[:50]}...")

            # Пишем в stdin через PTY writer
            self._writer.write(f"{prompt}\n".encode("utf-8"))
            await self._writer.drain()

            # Читаем stdout логи пока не получим event.is_done
            while True:
                line = await self._reader.readline()
                if not line:
                    logger.warning("Process EOF on stdout. Did it crash?")
                    # Процесс мог упасть
                    await self.kill()
                    break

                line_text = line.decode("utf-8").strip()
                event = GeminiStreamParser.parse_line(line_text)

                if event.text_chunk:
                    await on_chunk(event.text_chunk)

                if event.approval_request:
                    await on_approval(event.approval_request)
                    # Обычно после запроса аппрува CLI ждет ввода 'yes' или 'no'
                    break  # Выходим из цикла чтения, ждем действия пользователя

                if event.is_done:
                    # Ответ закончен
                    break

    async def answer_approval(self, answer: str) -> None:
        """Ответить на запрос подтверждения (yes/no/yolo)."""
        async with self.lock:
            if await self.is_alive():
                logger.info(f"Sending approval answer: {answer}")
                self._writer.write(f"{answer}\n".encode("utf-8"))
                await self._writer.drain()
