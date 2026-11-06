"""Shared model access. Business features depend on AIService.complete()."""

from .service import AIService, AIError

__all__ = ['AIService', 'AIError']
