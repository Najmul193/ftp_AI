"""The privacy gateway: the only path by which anything reaches an AI provider.

The pure parts -- tokenizer, generalize, dlp, policy, grounding -- do no I/O,
so the rules that keep bank data inside the bank are tested without a database
or a network. `gateway.py` composes them around the provider call.
"""
