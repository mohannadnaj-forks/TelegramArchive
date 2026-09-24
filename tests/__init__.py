import logging

# Tests that call modules directly leave logging unconfigured; without a handler, warnings would print.
logging.getLogger('telegram_archive').addHandler(logging.NullHandler())
