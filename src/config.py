import os
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from typing import List, Dict

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

class Config(BaseModel):
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMINS: List[int] = Field(default_factory=list)
    ROLLYPAY_API_KEY: str = os.getenv("ROLLYPAY_API_KEY", "")
    ROLLYPAY_TERMINAL_ID: str = os.getenv("ROLLYPAY_TERMINAL_ID", "")
    ROLLYPAY_SIGNING_SECRET: str = os.getenv("ROLLYPAY_SIGNING_SECRET", "")
    ROLLYPAY_TEST_MODE: bool = os.getenv("ROLLYPAY_TEST_MODE", "false").lower() == "true"
    PANEL_PASSWORD: str = os.getenv("PANEL_PASSWORD", "")
    XUI_API_URL: str = os.getenv("XUI_API_URL", "http://127.0.0.1:2053")
    XUI_API_TOKEN: str = os.getenv("XUI_API_TOKEN", "")
    XUI_VERIFY_SSL: bool = os.getenv("XUI_VERIFY_SSL", "false").lower() == "true"
    INBOUND_ID: int = int(os.getenv("INBOUND_ID", "1"))
    TOPVPN_EMAIL_STATS_URL: str = os.getenv("TOPVPN_EMAIL_STATS_URL", "")
    TOPVPN_EMAIL_STATS_TOKEN: str = os.getenv("TOPVPN_EMAIL_STATS_TOKEN", "")

    # Настройки цен и скидок
    PRICES: Dict[int, Dict[str, int]] = {
        1: {"base_price": 300, "discount_percent": 0},
        3: {"base_price": 850, "discount_percent": 0},
        6: {"base_price": 1600, "discount_percent": 0},
        12: {"base_price": 3000, "discount_percent": 0}
    }

    @field_validator('ADMINS', mode='before')
    def parse_admins(cls, value):
        if isinstance(value, str):
            return [int(admin) for admin in value.split(",") if admin.strip()]
        return value or []

    def calculate_price(self, months: int) -> int:
        """Вычисляет итоговую стоимость с учетом скидки"""
        if months not in self.PRICES:
            return 0
        
        price_info = self.PRICES[months]
        base_price = price_info["base_price"]
        discount_percent = price_info["discount_percent"]
        
        discount_amount = (base_price * discount_percent) // 100
        return base_price - discount_amount

config = Config(
    ADMINS=os.getenv("ADMINS", "")
)
