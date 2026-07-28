# PGKNMA Valentine

Django CMS с отделна Valentine платформа за училищни послания.

## Valentine функционалност

- вход с индивидуален шестсимволен код;
- защитени и изтичащи сесии;
- избор на получател по клас и UUID;
- пет визуални теми и live preview;
- анонимни или подписани послания;
- дневни лимити и cooldown;
- обратно броене и заключена пощенска кутия;
- входящи и изходящи послания;
- докладване и модерация през Django admin.

## Локално стартиране

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

По подразбиране локалната среда използва `db.sqlite3` и включва Django
development mode. Valentine е достъпна на:

```text
http://127.0.0.1:8000/valentines/
```

## Production

Копирай `.env.example` като отправна точка и конфигурирай стойностите чрез
защитени environment variables. Не добавяй `.env`, SQLite базата, качени
медии или production тайни в Git.

Допълнителните Valentine инструкции са в [VALENTINE.md](VALENTINE.md).
