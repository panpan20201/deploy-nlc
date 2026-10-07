"""
calibrate_partsnumber.py

Вспомогательный скрипт ТОЛЬКО для калибровки координат в partsnumber_date.py.
В штатной работе бота не используется, нужен один раз (или при смене
разрешения экрана/размера окна).

ЧТО ОН ДЕЛАЕТ: открывает настоящее окно браузера (видимое, не скрытое)
ФИКСИРОВАННОГО размера — такого же, как в partsnumber_date.py
(VIEWPORT_WIDTH x VIEWPORT_HEIGHT), заходит на страницу логина partsnumber
и добавляет в левый верхний угол жёлтый индикатор, который в реальном
времени показывает координаты курсора мыши.

КАК ПОЛЬЗОВАТЬСЯ (без программирования):
1. Запустить этот файл (команда: python calibrate_partsnumber.py).
2. В открывшемся окне браузера залогиниться вручную, как обычно.
3. Дождаться загрузки каталога (окно "Catalogs").
4. Навести мышь на нужный элемент (например на поле VIN) — в левом верхнем
   углу появятся числа вида "X: 123, Y: 45". Эти два числа и есть нужные
   координаты — записать их (просто текстом, в любом виде).
5. Повторить для каждого элемента интерфейса, координаты которого нужно уточнить.
6. Когда всё записано — просто закрыть окно браузера, скрипт завершится сам.

ВАЖНО: если реальный URL страницы логина отличается от указанного ниже —
поменять LOGIN_URL на правильный перед запуском.
"""

from playwright.sync_api import sync_playwright

# Должно совпадать с VIEWPORT_WIDTH/VIEWPORT_HEIGHT в partsnumber_date.py —
# иначе откалиброванные координаты не совпадут с тем, что видит основной скрипт.
VIEWPORT_WIDTH = 1600
VIEWPORT_HEIGHT = 900

LOGIN_URL = "https://login.partsnumber.com/"  # поправить, если реальный адрес другой

COORD_OVERLAY_JS = """
(() => {
  const box = document.createElement('div');
  box.style.position = 'fixed';
  box.style.top = '0';
  box.style.left = '0';
  box.style.zIndex = '2147483647';
  box.style.background = 'yellow';
  box.style.color = 'black';
  box.style.font = 'bold 18px monospace';
  box.style.padding = '4px 10px';
  box.style.pointerEvents = 'none';
  box.textContent = 'X: -, Y: -';

  // ВАЖНО: если сайт переключается в полноэкранный режим (Fullscreen API —
  // частая практика для веб-клиентов удалённого рабочего стола), браузер
  // рисует полноэкранный элемент в отдельном "верхнем слое", который
  // перекрывает АБСОЛЮТНО всё остальное на странице, даже элементы с
  // максимальным z-index. Поэтому индикатор нужно переносить внутрь
  // текущего полноэкранного элемента, иначе он "исчезнет" из вида,
  // хотя технически останется на странице.
  function attachBox() {
    const target = document.fullscreenElement || document.documentElement;
    if (box.parentElement !== target) {
      target.appendChild(box);
    }
  }

  document.addEventListener('fullscreenchange', attachBox, true);
  document.addEventListener('mousemove', (e) => {
    box.textContent = `X: ${e.clientX}, Y: ${e.clientY}`;
  }, true);

  attachBox();
  // Страховка на случай, если сайт входит в fullscreen нестандартным
  // способом без нормального события fullscreenchange.
  setInterval(attachBox, 500);
})();
"""


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT}
        )
        # add_init_script срабатывает на КАЖДОЙ новой странице/навигации —
        # важно, т.к. Horizon может уводить на другой адрес после логина,
        # а индикатор координат должен появиться и там тоже.
        context.add_init_script(COORD_OVERLAY_JS)

        page = context.new_page()
        page.goto(LOGIN_URL)

        print("=" * 70)
        print("Окно браузера открыто.")
        print("1. Залогинься вручную, как обычно.")
        print("2. Дождись загрузки каталога.")
        print("3. Наводи мышь на нужные элементы — координаты будут")
        print("   показаны жёлтым в левом верхнем углу окна браузера.")
        print("4. Когда закончишь калибровку — закрой окно браузера,")
        print("   скрипт сам завершится.")
        print("=" * 70)

        # Ждём, пока пользователь не закроет окно браузера вручную.
        page.wait_for_event("close", timeout=0)
        print("Окно браузера закрыто, скрипт завершён.")


if __name__ == "__main__":
    main()
