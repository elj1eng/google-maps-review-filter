"""Small fake Playwright objects shared by offline tests.

They model just the surface the scraper touches, so browser logic can be
driven deterministically without launching Chromium.
"""


class FakeElement:
    """One DOM node: text, click target, scroll metrics."""

    def __init__(
        self,
        text="",
        *,
        click_error=None,
        wait_error=None,
        evaluate_result=0,
        evaluate_error=None,
        box=None,
        box_error=None,
        child=None,
    ):
        self.text = text
        self.click_error = click_error
        self.wait_error = wait_error
        self.evaluate_result = evaluate_result
        self.evaluate_error = evaluate_error
        self.box = box
        self.box_error = box_error
        self.child = child
        self.clicks = 0
        self.waits = 0
        self.scrolls = 0
        self.evaluated = []

    def inner_text(self, timeout=None):
        return self.text

    def click(self, force=False):
        if self.click_error is not None:
            raise self.click_error
        self.clicks += 1

    def wait_for(self, state=None, timeout=None):
        self.waits += 1
        if self.wait_error is not None:
            raise self.wait_error

    def evaluate(self, script):
        self.evaluated.append(script)
        if self.evaluate_error is not None:
            raise self.evaluate_error
        return self.evaluate_result

    def bounding_box(self):
        if self.box_error is not None:
            raise self.box_error
        return self.box

    def scroll_into_view_if_needed(self, timeout=None):
        self.scrolls += 1

    def locator(self, selector):
        return self.child if self.child is not None else FakeLocator([])


class FakeLocator:
    """A selector result: a list of elements, with optional failures."""

    def __init__(self, elements=(), *, count_error=None, nth_error=None, count_fn=None):
        self.elements = list(elements)
        self.count_error = count_error
        self.nth_error = nth_error
        self.count_fn = count_fn

    def count(self):
        if self.count_error is not None:
            raise self.count_error
        if self.count_fn is not None:
            return self.count_fn()
        return len(self.elements)

    def nth(self, index):
        if self.nth_error is not None:
            raise self.nth_error
        return self.elements[index]

    @property
    def first(self):
        return self.elements[0] if self.elements else FakeElement()

    def locator(self, selector):
        return self.first.locator(selector)


class FakeKeyboard:
    def __init__(self, error=None):
        self.error = error
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)
        if self.error is not None:
            raise self.error


class FakeMouse:
    def __init__(self):
        self.moves = []
        self.wheels = []

    def move(self, x, y):
        self.moves.append((x, y))

    def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakePage:
    """A page whose selectors map to FakeLocators."""

    def __init__(
        self,
        locators=None,
        *,
        lang="en",
        limited_count=0,
        wait_selector_error=None,
        wait_function_error=None,
        content_html="<html></html>",
    ):
        self.locators = locators or {}
        self.lang = lang
        self.limited_count = limited_count
        self.wait_selector_error = wait_selector_error
        self.wait_function_error = wait_function_error
        self.content_html = content_html
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()
        self.gotos = []
        self.reloads = 0
        self.waited_selectors = []
        self.init_scripts = []

    def locator(self, selector):
        value = self.locators.get(selector, FakeLocator([]))
        if isinstance(value, Exception):
            raise value
        return value

    def get_by_text(self, text, exact=False):
        return FakeLocator([FakeElement()] * self.limited_count)

    def wait_for_selector(self, selector, timeout=None):
        self.waited_selectors.append(selector)
        if self.wait_selector_error is not None:
            raise self.wait_selector_error

    def wait_for_function(self, *args, **kwargs):
        if self.wait_function_error is not None:
            raise self.wait_function_error

    def evaluate(self, script):
        return self.lang

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def goto(self, url, wait_until=None):
        self.gotos.append((url, wait_until))

    def reload(self, wait_until=None):
        self.reloads += 1

    def content(self):
        return self.content_html


class FakeContext:
    """BrowserContext: cookies, pages, init scripts, storage state."""

    def __init__(self, page=None, *, cookies=None, add_cookies_error=None):
        self.pages = [page or FakePage()]
        self.cookie_calls = 0
        self.cookies_value = cookies or []
        self.added_cookies = []
        self.add_cookies_error = add_cookies_error
        self.init_scripts = []
        self.closed = False

    def cookies(self):
        self.cookie_calls += 1
        if callable(self.cookies_value):
            return self.cookies_value(self.cookie_calls)
        return self.cookies_value

    def add_cookies(self, cookies):
        if self.add_cookies_error is not None:
            raise self.add_cookies_error
        self.added_cookies.append(cookies)

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def storage_state(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"cookies": [], "origins": []}')

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, context):
        self.context = context
        self.calls = []

    def launch_persistent_context(self, profile_path, **kwargs):
        self.calls.append((profile_path, kwargs))
        return self.context


class FakePlaywright:
    def __init__(self, context):
        self.chromium = FakeChromium(context)
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakePlaywrightManager:
    """Stands in for ``sync_playwright()`` (context manager and .start())."""

    def __init__(self, playwright):
        self.playwright = playwright
        self.started = 0
        self.exited = False

    def start(self):
        self.started += 1
        return self.playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *exc_info):
        self.exited = True
        return False
