from abc import abstractmethod

from jiuwenclaw.extensions.sdk.base import BaseExtension
from jiuwenclaw.common.security.base_crypto import CryptoProvider


class CryptoUtility(BaseExtension):
    """擴充套件入口：持有真正的加解密實現，透過 `get_crypto()` 暴露。"""

    @abstractmethod
    def get_crypto(self) -> CryptoProvider:
        """返回實際執行 encrypt/decrypt 的例項。"""
        ...

    async def shutdown(self) -> None:
        """擴充套件關閉"""
        pass
