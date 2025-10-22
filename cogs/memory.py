from discord.ext import commands

from utils.console_display import log_success
from utils import data_manager

class MemoryCog(commands.Cog, name="MemoryCog"):
    def __init__(self, bot):
        self.bot = bot
        
        # ★ 修正: 'memory'キーから直接メモリデータを取得
        self.memories = data_manager.get_data('memory')
        
        log_success("MEMORY", f"{len(self.memories)}件の記憶を読み込みました。")

    def add_memory(self, memory_text: str):
        self.memories.append(memory_text)
        log_success("MEMORY", f"新しい記憶をメモリに追加: {memory_text}")

    def get_memories(self) -> list:
        return self.memories
    
    def delete_memory(self, index: int): 
        if 0 <= index < len(self.memories):
            removed_memory = self.memories.pop(index)
            log_success("MEMORY", f"記憶 No.{index+1} をメモリから削除しました。")
            return removed_memory
        return None

    def reset_memories(self):
        self.memories.clear()
        log_success("MEMORY", "メモリ上の記憶データがリセットされました。")

async def setup(bot):
    await bot.add_cog(MemoryCog(bot))