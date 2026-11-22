# One-shot startup

After completing the [requirements](requirements.md), run this from the cloned checkout in your Linux VM:

```bash
./bootstrap/remote-control.sh --once
```

The command prints **COPY THIS PROMPT INTO CHATGPT** and launches Remote Desktop Commander in the same terminal. Use the displayed connection details to connect from ChatGPT, then paste the prompt into the conversation.

Keep the terminal open while using the bridge. Press **Ctrl-C** to stop it; run the command again to reconnect. There is no automatic restart.

The launcher writes no kit configuration or state. npm may download the bridge package, ask for confirmation, and leave cache files or logs. The bridge also manages its own files.

Run only one bridge instance at a time. The one-shot lock applies to a single
checkout. Coordinate starts across checkouts and with the persistent service.
Stop any existing instance before starting one-shot mode, and do not start
the persistent service while one-shot mode is running.
