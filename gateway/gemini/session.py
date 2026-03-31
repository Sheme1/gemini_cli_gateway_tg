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
        
    async def spawn(self, resume: bool = False) -> None:
        """Запуск процесса gemini в interactive backend mode."""
        args = [
            "gemini",
            "-m", self.config.gemini_model,
            "-o", "stream-json",
        ]
        args.extend(self.config.approval_mode_flag)
        args.extend(self.config.sandbox_flag)
        
        if resume:
            args.extend(["--resume", "latest"])

        logger.info(f"Spawning Gemini CLI: {' '.join(args)}")
        
        self.process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.gemini_working_dir,
        )
        
        # Читаем начальный вывод (приветствие), чтобы очистить stdout
        # Запустим фоновую задачу для логирования stderr, чтобы не блокировать pipe
        asyncio.create_task(self._read_stderr(self.process.stderr))

    async def _read_stderr(self, stderr: asyncio.StreamReader) -> None:
        """Логирование ошибок CLI в фоне."""
        while True:
            line = await stderr.readline()
            if not line:
                break
            logger.error(f"[CLI STDERR] {line.decode().strip()}")

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
        on_approval: Callable[[dict], asyncio.Future]
    ) -> None:
        """Отправить промпт в процесс и стримить ответ через обратные вызовы."""
        async with self.lock:
            if not await self.is_alive():
                logger.info("Process is dead or not started. Spawning new one.")
                await self.spawn()
                
            logger.info(f"Sending prompt to process: {prompt[:50]}...")
            
            # Пишем в stdin
            self.process.stdin.write(f"{prompt}\n".encode("utf-8"))
            await self.process.stdin.drain()
            
            # Читаем stdout пока не получим event.is_done
            while True:
                line = await self.process.stdout.readline()
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
                    break # Выходим из цикла чтения, ждем действия пользователя
                    
                if event.is_done:
                    # Ответ закончен
                    break

    async def answer_approval(self, answer: str) -> None:
        """Ответить на запрос подтверждения (yes/no/yolo)."""
        async with self.lock:
            if await self.is_alive():
                logger.info(f"Sending approval answer: {answer}")
                self.process.stdin.write(f"{answer}\n".encode("utf-8"))
                await self.process.stdin.drain()
