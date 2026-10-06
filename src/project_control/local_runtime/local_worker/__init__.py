"""Canonical receiver package for the historical ``local_worker.*`` identity.

This initializer is receiver-owned: the supplier used a namespace package.
It makes this exact runtime root an ordinary package once the trusted binding
has validated its manifest and placed the receiver root on ``sys.path``.
"""
