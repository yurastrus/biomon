# SPDX-License-Identifier: AGPL-3.0-only
from app import create_app

# variable must be 'app' to match the service command in wuos.service
app = create_app()

if __name__ == "__main__":
    app.run()