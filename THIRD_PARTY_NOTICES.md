# Third-party acknowledgements

The session-cookie persistence mechanism in `backend/session_cookies.py` is
adapted from the Chromium CDP cookie handling approach in
[DSH Platform Account Manager Plugin](https://github.com/sycamorestr/dsh-platform-account-manager-plugin),
`src/browser.ts`, commit `4a7ca322101befafee1ff4f27cb8dc03bf57ddd6`.
This workbench retains its own Python/Playwright implementation, environment
registry, queue, login checks and interface. No DSH runtime is required.

MIT License

Copyright (c) 2026 DSH Platform Account Manager Plugin contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
