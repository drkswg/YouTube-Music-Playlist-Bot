"""Telegram presentation translations; music metadata is left as supplied.

English source strings are the catalog keys and the default language. Each
translation tuple follows the non-English order in LANGUAGES. Workflow errors
remain independent of presentation and are translated when shown, including
the small set of parameterized messages stored in existing drafts.
"""

import re


LANGUAGES = {
    "en": "English",
    "ru": "Русский",
    "es": "Español",
    "de": "Deutsch",
    "fr": "Français",
    "pt": "Português",
}

_TRANSLATION_LANGUAGES = tuple(code for code in LANGUAGES if code != "en")

_CATALOG: dict[str, tuple[str, ...]] = {
    "How to send a song list": (
        "Как отправить список песен", "Cómo enviar una lista de canciones",
        "So sendest du eine Titelliste", "Comment envoyer une liste de chansons",
        "Como enviar uma lista de músicas",
    ),
    "Continue the saved draft": (
        "Продолжить сохранённый черновик", "Continuar el borrador guardado",
        "Gespeicherten Entwurf fortsetzen", "Reprendre le brouillon enregistré",
        "Continuar o rascunho salvo",
    ),
    "Cancel the draft before creation": (
        "Отменить черновик до создания плейлиста", "Cancelar el borrador antes de crear la lista",
        "Entwurf vor der Erstellung abbrechen", "Annuler le brouillon avant la création",
        "Cancelar o rascunho antes da criação",
    ),
    "Change a recording: /edit <source line>": (
        "Изменить запись: /edit <номер строки>", "Cambiar grabación: /edit <número de línea>",
        "Aufnahme ändern: /edit <Zeilennummer>", "Changer d’enregistrement : /edit <numéro de ligne>",
        "Alterar gravação: /edit <número da linha>",
    ),
    "Choose the interface language": (
        "Выбрать язык интерфейса", "Elegir el idioma de la interfaz",
        "Sprache der Oberfläche wählen", "Choisir la langue de l’interface",
        "Escolher o idioma da interface",
    ),
    "Send a list with one song per line, for example Oasis — Wonderwall. Commands: /resume, /cancel, /edit <source line>, /language. Use the Menu button to see all commands.": (
        "Отправьте список: одна песня на строку, например Oasis — Wonderwall. Команды: /resume, /cancel, /edit <номер строки>, /language. Все команды доступны через кнопку меню.",
        "Envía una lista con una canción por línea, por ejemplo Oasis — Wonderwall. Comandos: /resume, /cancel, /edit <número de línea>, /language. Usa el botón Menú para ver todos los comandos.",
        "Sende eine Liste mit einem Titel pro Zeile, zum Beispiel Oasis — Wonderwall. Befehle: /resume, /cancel, /edit <Zeilennummer>, /language. Über die Menü-Schaltfläche siehst du alle Befehle.",
        "Envoyez une liste avec une chanson par ligne, par exemple Oasis — Wonderwall. Commandes : /resume, /cancel, /edit <numéro de ligne>, /language. Le bouton Menu affiche toutes les commandes.",
        "Envie uma lista com uma música por linha, por exemplo Oasis — Wonderwall. Comandos: /resume, /cancel, /edit <número da linha>, /language. Use o botão Menu para ver todos os comandos.",
    ),
    "Choose a language. Current language: {language}.": (
        "Выберите язык. Текущий язык: {language}.", "Elige un idioma. Idioma actual: {language}.",
        "Wähle eine Sprache. Aktuelle Sprache: {language}.", "Choisissez une langue. Langue actuelle : {language}.",
        "Escolha um idioma. Idioma atual: {language}.",
    ),
    "Unsupported language. Use /language or /language en, ru, es, de, fr, pt.": (
        "Этот язык не поддерживается. Используйте /language или /language en, ru, es, de, fr, pt.",
        "Idioma no compatible. Usa /language o /language en, ru, es, de, fr, pt.",
        "Diese Sprache wird nicht unterstützt. Nutze /language oder /language en, ru, es, de, fr, pt.",
        "Langue non prise en charge. Utilisez /language ou /language en, ru, es, de, fr, pt.",
        "Idioma não suportado. Use /language ou /language en, ru, es, de, fr, pt.",
    ),
    "Language saved: {language}. Use /resume to continue a saved draft or send a song list.": (
        "Язык сохранён: {language}. Используйте /resume, чтобы продолжить черновик, или отправьте список песен.",
        "Idioma guardado: {language}. Usa /resume para continuar un borrador o envía una lista de canciones.",
        "Sprache gespeichert: {language}. Setze einen Entwurf mit /resume fort oder sende eine Titelliste.",
        "Langue enregistrée : {language}. Utilisez /resume pour reprendre un brouillon ou envoyez une liste de chansons.",
        "Idioma salvo: {language}. Use /resume para continuar um rascunho ou envie uma lista de músicas.",
    ),
    "Draft cancelled.": (
        "Черновик отменён.", "Borrador cancelado.", "Entwurf abgebrochen.",
        "Brouillon annulé.", "Rascunho cancelado.",
    ),
    "No draft. Send a list of songs.": (
        "Нет черновика. Отправьте список песен.", "No hay borrador. Envía una lista de canciones.",
        "Kein Entwurf vorhanden. Sende eine Titelliste.", "Aucun brouillon. Envoyez une liste de chansons.",
        "Não há rascunho. Envie uma lista de músicas.",
    ),
    "First select a version using a button or use /cancel.": (
        "Сначала выберите версию кнопкой или используйте /cancel.",
        "Primero elige una versión con un botón o usa /cancel.",
        "Wähle zuerst eine Version über eine Schaltfläche oder nutze /cancel.",
        "Choisissez d’abord une version avec un bouton ou utilisez /cancel.",
        "Primeiro escolha uma versão com um botão ou use /cancel.",
    ),
    "Use the confirmation button or /resume.": (
        "Используйте кнопку подтверждения или /resume.", "Usa el botón de confirmación o /resume.",
        "Nutze die Bestätigungsschaltfläche oder /resume.", "Utilisez le bouton de confirmation ou /resume.",
        "Use o botão de confirmação ou /resume.",
    ),
    "Correct the lines that do not follow the Artist — Song format: {lines}": (
        "Исправьте строки, не соответствующие формату Исполнитель — Песня: {lines}",
        "Corrige las líneas que no siguen el formato Artista — Canción: {lines}",
        "Korrigiere die Zeilen, die nicht dem Format Interpret — Titel entsprechen: {lines}",
        "Corrigez les lignes qui ne respectent pas le format Artiste — Chanson : {lines}",
        "Corrija as linhas que não seguem o formato Artista — Música: {lines}",
    ),
    "Send a new search query for this song.": (
        "Отправьте новый поисковый запрос для этой песни.", "Envía una nueva búsqueda para esta canción.",
        "Sende eine neue Suchanfrage für diesen Titel.", "Envoyez une nouvelle recherche pour cette chanson.",
        "Envie uma nova pesquisa para esta música.",
    ),
    "Invalid button; use /resume.": (
        "Недействительная кнопка; используйте /resume.", "Botón no válido; usa /resume.",
        "Ungültige Schaltfläche; nutze /resume.", "Bouton non valide ; utilisez /resume.",
        "Botão inválido; use /resume.",
    ),
    "The playlist is still being created. Please wait.": (
        "Плейлист ещё создаётся. Подождите.", "La lista aún se está creando. Espera, por favor.",
        "Die Playlist wird noch erstellt. Bitte warten.", "La playlist est en cours de création. Veuillez patienter.",
        "A playlist ainda está sendo criada. Aguarde.",
    ),
    "Gemini is choosing a recording for source line {line} ({total} songs). Please wait.": (
        "Gemini выбирает запись для строки {line} (всего песен: {total}). Подождите.",
        "Gemini está eligiendo una grabación para la línea {line} ({total} canciones). Espera, por favor.",
        "Gemini wählt eine Aufnahme für Zeile {line} ({total} Titel). Bitte warten.",
        "Gemini choisit un enregistrement pour la ligne {line} ({total} chansons). Veuillez patienter.",
        "Gemini está escolhendo uma gravação para a linha {line} ({total} músicas). Aguarde.",
    ),
    "{index}/{total}: waiting for a new search query for {artist} — {title}.": (
        "{index}/{total}: ожидается новый поисковый запрос для {artist} — {title}.",
        "{index}/{total}: esperando una nueva búsqueda para {artist} — {title}.",
        "{index}/{total}: Neue Suchanfrage für {artist} — {title} erwartet.",
        "{index}/{total} : en attente d’une nouvelle recherche pour {artist} — {title}.",
        "{index}/{total}: aguardando uma nova pesquisa para {artist} — {title}.",
    ),
    "Try another search": (
        "Другой поиск", "Otra búsqueda", "Andere Suche", "Autre recherche", "Outra pesquisa",
    ),
    "Skip": ("Пропустить", "Omitir", "Überspringen", "Passer", "Pular"),
    "Please confirm a recording from the saved search results.": (
        "Подтвердите запись из сохранённых результатов поиска.",
        "Confirma una grabación de los resultados guardados.",
        "Bestätige eine Aufnahme aus den gespeicherten Suchergebnissen.",
        "Confirmez un enregistrement parmi les résultats enregistrés.",
        "Confirme uma gravação dos resultados salvos.",
    ),
    " · album: {album}": (
        " · альбом: {album}", " · álbum: {album}", " · Album: {album}",
        " · album : {album}", " · álbum: {album}",
    ),
    "Showing 5 of {total} results; {hidden} more are hidden. Try another search to narrow the results.": (
        "Показаны 5 из {total} результатов; ещё {hidden} скрыто. Уточните поисковый запрос.",
        "Se muestran 5 de {total} resultados; hay {hidden} más ocultos. Prueba otra búsqueda para acotar los resultados.",
        "5 von {total} Ergebnissen werden angezeigt; {hidden} weitere sind ausgeblendet. Grenze die Ergebnisse mit einer anderen Suche ein.",
        "5 résultats sur {total} sont affichés ; {hidden} autres sont masqués. Affinez la recherche.",
        "Exibindo 5 de {total} resultados; outros {hidden} estão ocultos. Refine a pesquisa.",
    ),
    "Selection complete. Send a title for the new playlist.": (
        "Выбор завершён. Отправьте название нового плейлиста.",
        "Selección completada. Envía un título para la nueva lista.",
        "Auswahl abgeschlossen. Sende einen Namen für die neue Playlist.",
        "Sélection terminée. Envoyez un titre pour la nouvelle playlist.",
        "Seleção concluída. Envie um título para a nova playlist.",
    ),
    " · duplicate will be skipped": (
        " · повтор будет пропущен", " · se omitirá el duplicado", " · Duplikat wird übersprungen",
        " · le doublon sera ignoré", " · a duplicata será ignorada",
    ),
    "Skipped lines: {lines}": (
        "Пропущенные строки: {lines}", "Líneas omitidas: {lines}", "Übersprungene Zeilen: {lines}",
        "Lignes ignorées : {lines}", "Linhas ignoradas: {lines}",
    ),
    "{title}: selected recordings": (
        "{title}: выбранные записи", "{title}: grabaciones seleccionadas", "{title}: ausgewählte Aufnahmen",
        "{title} : enregistrements sélectionnés", "{title}: gravações selecionadas",
    ),
    "Create private playlist": (
        "Создать приватный плейлист", "Crear lista privada", "Private Playlist erstellen",
        "Créer une playlist privée", "Criar playlist privada",
    ),
    "Cancel": ("Отмена", "Cancelar", "Abbrechen", "Annuler", "Cancelar"),
    "Reviewed {count} selected entries. Use /edit <source line> to change a recording. Create a private playlist?": (
        "Выбрано записей: {count}. Для изменения записи используйте /edit <номер строки>. Создать приватный плейлист?",
        "Se revisaron {count} entradas seleccionadas. Usa /edit <número de línea> para cambiar una grabación. ¿Crear una lista privada?",
        "{count} ausgewählte Einträge geprüft. Ändere eine Aufnahme mit /edit <Zeilennummer>. Private Playlist erstellen?",
        "{count} entrées sélectionnées vérifiées. Utilisez /edit <numéro de ligne> pour changer un enregistrement. Créer une playlist privée ?",
        "{count} entradas selecionadas revisadas. Use /edit <número da linha> para alterar uma gravação. Criar uma playlist privada?",
    ),
    "Writing is not complete yet. Use /resume.": (
        "Добавление песен ещё не завершено. Используйте /resume.", "La escritura aún no ha terminado. Usa /resume.",
        "Das Hinzufügen ist noch nicht abgeschlossen. Nutze /resume.", "L’ajout n’est pas terminé. Utilisez /resume.",
        "A inclusão ainda não foi concluída. Use /resume.",
    ),
    "Done: {added} songs. {url}": (
        "Готово. Добавлено песен: {added}. {url}", "Listo: {added} canciones. {url}",
        "Fertig: {added} Titel. {url}", "Terminé : {added} chansons. {url}", "Pronto: {added} músicas. {url}",
    ),
    "I checked the playlist; retry insertion": (
        "Плейлист проверен; повторить добавление", "Revisé la lista; reintentar inserción",
        "Playlist geprüft; erneut hinzufügen", "Playlist vérifiée ; réessayer l’ajout",
        "Verifiquei a playlist; tentar adicionar novamente",
    ),
    "The outcome of the previous insertion is unknown. Open the playlist and make sure this recording is absent. Retrying may create a duplicate if YouTube shows the recording later.\n": (
        "Результат предыдущего добавления неизвестен. Откройте плейлист и убедитесь, что этой записи нет. Повторная попытка может создать дубликат, если YouTube покажет запись позже.\n",
        "Se desconoce el resultado de la inserción anterior. Abre la lista y verifica que esta grabación no esté. Reintentar puede crear un duplicado si YouTube la muestra más tarde.\n",
        "Das Ergebnis des letzten Hinzufügens ist unbekannt. Öffne die Playlist und prüfe, ob diese Aufnahme fehlt. Ein erneuter Versuch kann ein Duplikat erzeugen, wenn YouTube die Aufnahme später anzeigt.\n",
        "Le résultat de l’ajout précédent est inconnu. Ouvrez la playlist et vérifiez que cet enregistrement est absent. Une nouvelle tentative peut créer un doublon si YouTube l’affiche plus tard.\n",
        "O resultado da inclusão anterior é desconhecido. Abra a playlist e verifique se esta gravação está ausente. Tentar novamente pode criar uma duplicata se o YouTube mostrar a gravação mais tarde.\n",
    ),
    "Error: {detail}\nUse /resume to continue.": (
        "Ошибка: {detail}\nИспользуйте /resume, чтобы продолжить.",
        "Error: {detail}\nUsa /resume para continuar.",
        "Fehler: {detail}\nNutze /resume zum Fortsetzen.",
        "Erreur : {detail}\nUtilisez /resume pour continuer.",
        "Erro: {detail}\nUse /resume para continuar.",
    ),
    "No matches. Try another query or skip this song.": (
        "Совпадений нет. Попробуйте другой запрос или пропустите песню.",
        "No hay coincidencias. Prueba otra búsqueda u omite esta canción.",
        "Keine Treffer. Versuche eine andere Suche oder überspringe diesen Titel.",
        "Aucun résultat. Essayez une autre recherche ou passez cette chanson.",
        "Nenhum resultado. Tente outra pesquisa ou pule esta música.",
    ),
    "The title and artist do not match confidently. Please choose a recording.": (
        "Не удалось уверенно сопоставить название и исполнителя. Выберите запись.",
        "El título y el artista no coinciden con suficiente certeza. Elige una grabación.",
        "Titel und Interpret stimmen nicht eindeutig überein. Wähle eine Aufnahme.",
        "Le titre et l’artiste ne correspondent pas avec certitude. Choisissez un enregistrement.",
        "O título e o artista não correspondem com segurança. Escolha uma gravação.",
    ),
    "The requested version was not found (a studio recording is preferred by default). Please choose a version or try another search.": (
        "Запрошенная версия не найдена (по умолчанию предпочтительна студийная запись). Выберите версию или попробуйте другой поиск.",
        "No se encontró la versión solicitada (se prefiere una grabación de estudio por defecto). Elige una versión o prueba otra búsqueda.",
        "Die gewünschte Version wurde nicht gefunden (standardmäßig wird eine Studioaufnahme bevorzugt). Wähle eine Version oder versuche eine andere Suche.",
        "La version demandée est introuvable (une version studio est préférée par défaut). Choisissez une version ou essayez une autre recherche.",
        "A versão solicitada não foi encontrada (a gravação de estúdio é preferida por padrão). Escolha uma versão ou tente outra pesquisa.",
    ),
    "Some version labels are unclear or conflicting. Please check the recordings.": (
        "Некоторые обозначения версий неясны или противоречат друг другу. Проверьте записи.",
        "Algunas etiquetas de versión son ambiguas o contradictorias. Revisa las grabaciones.",
        "Einige Versionsangaben sind unklar oder widersprüchlich. Prüfe die Aufnahmen.",
        "Certaines indications de version sont ambiguës ou contradictoires. Vérifiez les enregistrements.",
        "Algumas indicações de versão são ambíguas ou contraditórias. Verifique as gravações.",
    ),
    "Several recordings match the requested version. Please choose one.": (
        "Запрошенной версии соответствуют несколько записей. Выберите одну.",
        "Varias grabaciones coinciden con la versión solicitada. Elige una.",
        "Mehrere Aufnahmen passen zur gewünschten Version. Wähle eine aus.",
        "Plusieurs enregistrements correspondent à la version demandée. Choisissez-en un.",
        "Várias gravações correspondem à versão solicitada. Escolha uma.",
    ),
    "Matching recordings have different artist credits. Please choose one.": (
        "У подходящих записей различается состав исполнителей. Выберите одну.",
        "Las grabaciones coincidentes tienen distintos artistas acreditados. Elige una.",
        "Die passenden Aufnahmen nennen unterschiedliche Interpreten. Wähle eine aus.",
        "Les enregistrements correspondants mentionnent des artistes différents. Choisissez-en un.",
        "As gravações correspondentes têm créditos de artistas diferentes. Escolha uma.",
    ),
    "Several releases match, but their durations are missing or invalid. Please choose a recording.": (
        "Подходят несколько релизов, но их длительность неизвестна или некорректна. Выберите запись.",
        "Coinciden varios lanzamientos, pero sus duraciones faltan o no son válidas. Elige una grabación.",
        "Mehrere Veröffentlichungen passen, aber ihre Dauer fehlt oder ist ungültig. Wähle eine Aufnahme.",
        "Plusieurs éditions correspondent, mais leurs durées sont absentes ou invalides. Choisissez un enregistrement.",
        "Vários lançamentos correspondem, mas suas durações estão ausentes ou inválidas. Escolha uma gravação.",
    ),
    "Matching releases have different durations and may be different recordings. Please choose one.": (
        "У подходящих релизов разная длительность; это могут быть разные записи. Выберите одну.",
        "Los lanzamientos coincidentes tienen distintas duraciones y pueden ser grabaciones diferentes. Elige uno.",
        "Die passenden Veröffentlichungen haben unterschiedliche Längen und könnten verschiedene Aufnahmen sein. Wähle eine aus.",
        "Les éditions correspondantes ont des durées différentes et peuvent contenir des enregistrements différents. Choisissez-en une.",
        "Os lançamentos correspondentes têm durações diferentes e podem conter gravações distintas. Escolha um.",
    ),
    "Please choose a recording from your search results.": (
        "Выберите запись из результатов поиска.", "Elige una grabación de tus resultados de búsqueda.",
        "Wähle eine Aufnahme aus deinen Suchergebnissen.", "Choisissez un enregistrement parmi les résultats de votre recherche.",
        "Escolha uma gravação dos resultados da pesquisa.",
    ),
    "Automatic selection was interrupted. The saved search will not be sent again. Please choose manually.": (
        "Автоматический выбор прерван. Сохранённый поиск не будет отправлен повторно. Выберите запись вручную.",
        "La selección automática se interrumpió. La búsqueda guardada no se enviará de nuevo. Elige manualmente.",
        "Die automatische Auswahl wurde unterbrochen. Die gespeicherte Suche wird nicht erneut gesendet. Wähle manuell.",
        "La sélection automatique a été interrompue. La recherche enregistrée ne sera pas renvoyée. Choisissez manuellement.",
        "A seleção automática foi interrompida. A pesquisa salva não será enviada novamente. Escolha manualmente.",
    ),
    "Gemini could not choose confidently. Please choose manually.": (
        "Gemini не смог уверенно выбрать запись. Выберите вручную.",
        "Gemini no pudo elegir con certeza. Elige manualmente.",
        "Gemini konnte keine sichere Auswahl treffen. Wähle manuell.",
        "Gemini n’a pas pu choisir avec certitude. Choisissez manuellement.",
        "Gemini não conseguiu escolher com segurança. Escolha manualmente.",
    ),
    "Please choose manually.": (
        "Выберите вручную.", "Elige manualmente.", "Wähle manuell.",
        "Choisissez manuellement.", "Escolha manualmente.",
    ),
    "Automatic selection is unavailable.": (
        "Автоматический выбор недоступен.", "La selección automática no está disponible.",
        "Die automatische Auswahl ist nicht verfügbar.", "La sélection automatique est indisponible.",
        "A seleção automática está indisponível.",
    ),
    "Gemini rejected the API key. Check GEMINI_API_KEY.": (
        "Gemini отклонил ключ API. Проверьте GEMINI_API_KEY.", "Gemini rechazó la clave API. Revisa GEMINI_API_KEY.",
        "Gemini hat den API-Schlüssel abgelehnt. Prüfe GEMINI_API_KEY.", "Gemini a rejeté la clé API. Vérifiez GEMINI_API_KEY.",
        "Gemini rejeitou a chave API. Verifique GEMINI_API_KEY.",
    ),
    "Gemini API is disabled for this project. Check the project configuration.": (
        "Gemini API отключён для этого проекта. Проверьте настройки проекта.",
        "La API de Gemini está desactivada para este proyecto. Revisa su configuración.",
        "Die Gemini-API ist für dieses Projekt deaktiviert. Prüfe die Projektkonfiguration.",
        "L’API Gemini est désactivée pour ce projet. Vérifiez sa configuration.",
        "A API do Gemini está desativada para este projeto. Verifique sua configuração.",
    ),
    "The API key does not allow Gemini. Check its API restrictions.": (
        "Ключ API не разрешает доступ к Gemini. Проверьте ограничения API.",
        "La clave API no permite usar Gemini. Revisa sus restricciones de API.",
        "Der API-Schlüssel erlaubt Gemini nicht. Prüfe seine API-Einschränkungen.",
        "La clé API n’autorise pas Gemini. Vérifiez ses restrictions d’API.",
        "A chave API não permite usar Gemini. Verifique suas restrições de API.",
    ),
    "The API key blocks this connection. Check its IP restrictions.": (
        "Ключ API блокирует это соединение. Проверьте ограничения по IP.",
        "La clave API bloquea esta conexión. Revisa sus restricciones de IP.",
        "Der API-Schlüssel blockiert diese Verbindung. Prüfe seine IP-Einschränkungen.",
        "La clé API bloque cette connexion. Vérifiez ses restrictions IP.",
        "A chave API bloqueia esta conexão. Verifique suas restrições de IP.",
    ),
    "Gemini did not respond within the connection timeout.": (
        "Gemini не ответил за отведённое время.", "Gemini no respondió dentro del tiempo de espera de la conexión.",
        "Gemini hat nicht innerhalb des Verbindungszeitlimits geantwortet.", "Gemini n’a pas répondu dans le délai de connexion.",
        "Gemini não respondeu dentro do tempo limite da conexão.",
    ),
    "Gemini's response was truncated before completing a decision.": (
        "Ответ Gemini обрезан до завершения выбора.", "La respuesta de Gemini se truncó antes de completar la decisión.",
        "Die Antwort von Gemini wurde vor einer vollständigen Entscheidung gekürzt.",
        "La réponse de Gemini a été tronquée avant la fin de la décision.",
        "A resposta do Gemini foi truncada antes de concluir a decisão.",
    ),
    "Gemini blocked this decision through its safety checks.": (
        "Проверки безопасности Gemini заблокировали выбор.", "Las comprobaciones de seguridad de Gemini bloquearon esta decisión.",
        "Die Sicherheitsprüfungen von Gemini haben diese Entscheidung blockiert.",
        "Les contrôles de sécurité de Gemini ont bloqué cette décision.",
        "As verificações de segurança do Gemini bloquearam esta decisão.",
    ),
    "Gemini blocked this decision through its content checks.": (
        "Проверки содержимого Gemini заблокировали выбор.", "Los controles de contenido de Gemini bloquearon esta decisión.",
        "Die Inhaltsprüfungen von Gemini haben diese Entscheidung blockiert.",
        "Les contrôles de contenu de Gemini ont bloqué cette décision.",
        "As verificações de conteúdo do Gemini bloquearam esta decisão.",
    ),
    "Gemini rejected the request. Check the API key and project configuration.": (
        "Gemini отклонил запрос. Проверьте ключ API и настройки проекта.",
        "Gemini rechazó la solicitud. Revisa la clave API y la configuración del proyecto.",
        "Gemini hat die Anfrage abgelehnt. Prüfe API-Schlüssel und Projektkonfiguration.",
        "Gemini a rejeté la requête. Vérifiez la clé API et la configuration du projet.",
        "Gemini rejeitou a solicitação. Verifique a chave API e a configuração do projeto.",
    ),
    "Gemini authentication failed. Check GEMINI_API_KEY.": (
        "Ошибка аутентификации Gemini. Проверьте GEMINI_API_KEY.",
        "Falló la autenticación de Gemini. Revisa GEMINI_API_KEY.",
        "Gemini-Authentifizierung fehlgeschlagen. Prüfe GEMINI_API_KEY.",
        "L’authentification Gemini a échoué. Vérifiez GEMINI_API_KEY.",
        "A autenticação do Gemini falhou. Verifique GEMINI_API_KEY.",
    ),
    "Gemini access is denied. Check project access, API restrictions and regional availability.": (
        "Доступ к Gemini запрещён. Проверьте доступ к проекту, ограничения API и доступность в регионе.",
        "Acceso a Gemini denegado. Revisa el acceso al proyecto, las restricciones de API y la disponibilidad regional.",
        "Zugriff auf Gemini verweigert. Prüfe Projektzugriff, API-Einschränkungen und regionale Verfügbarkeit.",
        "L’accès à Gemini est refusé. Vérifiez l’accès au projet, les restrictions d’API et la disponibilité régionale.",
        "Acesso ao Gemini negado. Verifique o acesso ao projeto, as restrições de API e a disponibilidade regional.",
    ),
    "The configured Gemini model or endpoint is unavailable for this project.": (
        "Настроенная модель или адрес API Gemini недоступны для этого проекта.",
        "El modelo o endpoint de Gemini configurado no está disponible para este proyecto.",
        "Das konfigurierte Gemini-Modell oder der Endpunkt ist für dieses Projekt nicht verfügbar.",
        "Le modèle ou le point d’accès Gemini configuré est indisponible pour ce projet.",
        "O modelo ou endpoint do Gemini configurado está indisponível para este projeto.",
    ),
    "Gemini's free quota or request limit has been reached. Check AI Studio limits.": (
        "Достигнут предел бесплатной квоты или запросов Gemini. Проверьте лимиты AI Studio.",
        "Se alcanzó la cuota gratuita o el límite de solicitudes de Gemini. Revisa los límites de AI Studio.",
        "Das kostenlose Kontingent oder Anfragelimit von Gemini ist erreicht. Prüfe die Limits in AI Studio.",
        "Le quota gratuit ou la limite de requêtes Gemini est atteint. Vérifiez les limites dans AI Studio.",
        "A cota gratuita ou o limite de solicitações do Gemini foi atingido. Verifique os limites no AI Studio.",
    ),
    "Gemini could not be reached due to a network or connection error.": (
        "Не удалось связаться с Gemini из-за ошибки сети или соединения.",
        "No se pudo contactar con Gemini debido a un error de red o conexión.",
        "Gemini war wegen eines Netzwerk- oder Verbindungsfehlers nicht erreichbar.",
        "Gemini est injoignable en raison d’une erreur réseau ou de connexion.",
        "Não foi possível acessar o Gemini devido a um erro de rede ou conexão.",
    ),
    "Gemini returned an invalid or incomplete decision.": (
        "Gemini вернул некорректное или неполное решение.", "Gemini devolvió una decisión no válida o incompleta.",
        "Gemini hat eine ungültige oder unvollständige Entscheidung zurückgegeben.",
        "Gemini a renvoyé une décision invalide ou incomplète.", "Gemini retornou uma decisão inválida ou incompleta.",
    ),
    "The search metadata exceeds the automatic selection request limit.": (
        "Данные поиска превышают допустимый размер запроса автоматического выбора.",
        "Los metadatos de búsqueda superan el límite de la solicitud de selección automática.",
        "Die Suchmetadaten überschreiten das Anfragelimit der automatischen Auswahl.",
        "Les métadonnées de recherche dépassent la limite de requête de sélection automatique.",
        "Os metadados da pesquisa excedem o limite da solicitação de seleção automática.",
    ),
    "Gemini's response exceeds the automatic selection response limit.": (
        "Ответ Gemini превышает допустимый размер ответа автоматического выбора.",
        "La respuesta de Gemini supera el límite de respuesta de selección automática.",
        "Die Antwort von Gemini überschreitet das Antwortlimit der automatischen Auswahl.",
        "La réponse de Gemini dépasse la limite de réponse de sélection automatique.",
        "A resposta do Gemini excede o limite de resposta da seleção automática.",
    ),
    "Use /edit <source line number> from the review": (
        "Для изменения записи используйте /edit <номер строки> на этапе проверки",
        "Usa /edit <número de línea> desde la revisión", "Nutze /edit <Zeilennummer> in der Übersicht",
        "Utilisez /edit <numéro de ligne> à l’étape de vérification", "Use /edit <número da linha> na revisão",
    ),
    "Invalid button": (
        "Недействительная кнопка", "Botón no válido", "Ungültige Schaltfläche", "Bouton non valide", "Botão inválido",
    ),
    "This button belongs to an old draft": (
        "Эта кнопка относится к старому черновику", "Este botón pertenece a un borrador anterior",
        "Diese Schaltfläche gehört zu einem alten Entwurf", "Ce bouton appartient à un ancien brouillon",
        "Este botão pertence a um rascunho antigo",
    ),
    "This review is outdated; use /resume": (
        "Эта проверка устарела; используйте /resume", "Esta revisión está desactualizada; usa /resume",
        "Diese Übersicht ist veraltet; nutze /resume", "Cette vérification est périmée ; utilisez /resume",
        "Esta revisão está desatualizada; use /resume",
    ),
    "The list is empty. Format: Artist — Song": (
        "Список пуст. Формат: Исполнитель — Песня", "La lista está vacía. Formato: Artista — Canción",
        "Die Liste ist leer. Format: Interpret — Titel", "La liste est vide. Format : Artiste — Chanson",
        "A lista está vazia. Formato: Artista — Música",
    ),
    "A draft already exists. Use /resume or /cancel": (
        "Черновик уже существует. Используйте /resume или /cancel",
        "Ya existe un borrador. Usa /resume o /cancel", "Ein Entwurf existiert bereits. Nutze /resume oder /cancel",
        "Un brouillon existe déjà. Utilisez /resume ou /cancel", "Já existe um rascunho. Use /resume ou /cancel",
    ),
    "All songs have been skipped; use /cancel": (
        "Все песни пропущены; используйте /cancel", "Se omitieron todas las canciones; usa /cancel",
        "Alle Titel wurden übersprungen; nutze /cancel", "Toutes les chansons ont été ignorées ; utilisez /cancel",
        "Todas as músicas foram ignoradas; use /cancel",
    ),
    "Enter a search query between 1 and 200 characters long": (
        "Введите поисковый запрос длиной от 1 до 200 символов", "Introduce una búsqueda de entre 1 y 200 caracteres",
        "Gib eine Suchanfrage mit 1 bis 200 Zeichen ein", "Saisissez une recherche de 1 à 200 caractères",
        "Digite uma pesquisa com 1 a 200 caracteres",
    ),
    "This option is outdated; use /resume": (
        "Этот вариант устарел; используйте /resume", "Esta opción está desactualizada; usa /resume",
        "Diese Option ist veraltet; nutze /resume", "Cette option est périmée ; utilisez /resume",
        "Esta opção está desatualizada; use /resume",
    ),
    "Editing is available only before playlist creation": (
        "Редактирование доступно только до начала создания плейлиста",
        "Solo se puede editar antes de crear la lista", "Bearbeiten ist nur vor der Playlist-Erstellung möglich",
        "La modification est possible uniquement avant la création de la playlist",
        "A edição está disponível apenas antes da criação da playlist",
    ),
    "No song on that source line": (
        "В этой строке нет песни", "No hay ninguna canción en esa línea", "Kein Titel in dieser Zeile",
        "Aucune chanson sur cette ligne", "Não há música nessa linha",
    ),
    "A title is not expected at this stage": (
        "На этом этапе название не ожидается", "No se espera un título en esta etapa",
        "In diesem Schritt wird kein Name erwartet", "Aucun titre n’est attendu à cette étape",
        "Não é esperado um título nesta etapa",
    ),
    "The title must be between 1 and 150 characters long": (
        "Название должно содержать от 1 до 150 символов", "El título debe tener entre 1 y 150 caracteres",
        "Der Name muss 1 bis 150 Zeichen lang sein", "Le titre doit contenir de 1 à 150 caractères",
        "O título deve ter entre 1 e 150 caracteres",
    ),
    "Creation has already started. Use /resume": (
        "Создание уже началось. Используйте /resume", "La creación ya comenzó. Usa /resume",
        "Die Erstellung hat bereits begonnen. Nutze /resume", "La création a déjà commencé. Utilisez /resume",
        "A criação já começou. Use /resume",
    ),
    "Retrying the insertion is unavailable": (
        "Повторное добавление недоступно", "No se puede reintentar la inserción",
        "Erneutes Hinzufügen ist nicht verfügbar", "Une nouvelle tentative d’ajout est indisponible",
        "Não é possível tentar a inclusão novamente",
    ),
    "This recording has already been processed; use /resume": (
        "Эта запись уже обработана; используйте /resume", "Esta grabación ya fue procesada; usa /resume",
        "Diese Aufnahme wurde bereits verarbeitet; nutze /resume", "Cet enregistrement a déjà été traité ; utilisez /resume",
        "Esta gravação já foi processada; use /resume",
    ),
    "This button belongs to an old insertion attempt; use /resume": (
        "Эта кнопка относится к старой попытке добавления; используйте /resume",
        "Este botón pertenece a un intento anterior de inserción; usa /resume",
        "Diese Schaltfläche gehört zu einem alten Hinzufügeversuch; nutze /resume",
        "Ce bouton appartient à une ancienne tentative d’ajout ; utilisez /resume",
        "Este botão pertence a uma tentativa antiga de inclusão; use /resume",
    ),
    "The playlist contents were changed outside the bot; writing has stopped": (
        "Содержимое плейлиста изменено вне бота; добавление остановлено",
        "El contenido de la lista cambió fuera del bot; la escritura se detuvo",
        "Der Playlist-Inhalt wurde außerhalb des Bots geändert; das Hinzufügen wurde gestoppt",
        "Le contenu de la playlist a été modifié en dehors du bot ; l’ajout est arrêté",
        "O conteúdo da playlist foi alterado fora do bot; a inclusão foi interrompida",
    ),
    "No response was received for playlist creation. Use /resume to check": (
        "Ответ на создание плейлиста не получен. Используйте /resume для проверки",
        "No se recibió respuesta al crear la lista. Usa /resume para comprobar",
        "Keine Antwort zur Playlist-Erstellung erhalten. Prüfe mit /resume",
        "Aucune réponse reçue pour la création de la playlist. Utilisez /resume pour vérifier",
        "Nenhuma resposta recebida para a criação da playlist. Use /resume para verificar",
    ),
    "The outcome of creation is unknown. Check your playlists manually; automatic retry is blocked": (
        "Результат создания неизвестен. Проверьте плейлисты вручную; автоматический повтор заблокирован",
        "Se desconoce el resultado de la creación. Revisa tus listas manualmente; el reintento automático está bloqueado",
        "Das Ergebnis der Erstellung ist unbekannt. Prüfe deine Playlists manuell; automatisches Wiederholen ist gesperrt",
        "Le résultat de la création est inconnu. Vérifiez vos playlists manuellement ; la nouvelle tentative automatique est bloquée",
        "O resultado da criação é desconhecido. Verifique suas playlists manualmente; a nova tentativa automática está bloqueada",
    ),
    "The draft is not ready for playlist creation yet": (
        "Черновик ещё не готов к созданию плейлиста", "El borrador aún no está listo para crear la lista",
        "Der Entwurf ist noch nicht zur Playlist-Erstellung bereit", "Le brouillon n’est pas encore prêt pour la création de la playlist",
        "O rascunho ainda não está pronto para criar a playlist",
    ),
    "A previously added recording is missing from the playlist; writing has stopped": (
        "Ранее добавленная запись отсутствует в плейлисте; добавление остановлено",
        "Falta una grabación añadida previamente; la escritura se detuvo",
        "Eine zuvor hinzugefügte Aufnahme fehlt in der Playlist; das Hinzufügen wurde gestoppt",
        "Un enregistrement déjà ajouté est absent de la playlist ; l’ajout est arrêté",
        "Uma gravação adicionada anteriormente está ausente da playlist; a inclusão foi interrompida",
    ),
    "The insertion request was not sent. Fix authorization or network issues and use /resume": (
        "Запрос на добавление не отправлен. Исправьте авторизацию или проблемы сети и используйте /resume",
        "La solicitud de inserción no se envió. Soluciona los problemas de autorización o red y usa /resume",
        "Die Anfrage zum Hinzufügen wurde nicht gesendet. Behebe Autorisierungs- oder Netzwerkprobleme und nutze /resume",
        "La requête d’ajout n’a pas été envoyée. Corrigez les problèmes d’autorisation ou de réseau et utilisez /resume",
        "A solicitação de inclusão não foi enviada. Corrija os problemas de autorização ou rede e use /resume",
    ),
    "The draft changed during selection; use /resume": (
        "Черновик изменился во время выбора; используйте /resume", "El borrador cambió durante la selección; usa /resume",
        "Der Entwurf wurde während der Auswahl geändert; nutze /resume", "Le brouillon a changé pendant la sélection ; utilisez /resume",
        "O rascunho mudou durante a seleção; use /resume",
    ),
    "The search changed during selection; use /resume": (
        "Поиск изменился во время выбора; используйте /resume", "La búsqueda cambió durante la selección; usa /resume",
        "Die Suche wurde während der Auswahl geändert; nutze /resume", "La recherche a changé pendant la sélection ; utilisez /resume",
        "A pesquisa mudou durante a seleção; use /resume",
    ),
    "This selection is outdated; use /resume": (
        "Этот выбор устарел; используйте /resume", "Esta selección está desactualizada; usa /resume",
        "Diese Auswahl ist veraltet; nutze /resume", "Cette sélection est périmée ; utilisez /resume",
        "Esta seleção está desatualizada; use /resume",
    ),
    "The search results have changed; use /resume": (
        "Результаты поиска изменились; используйте /resume", "Los resultados de búsqueda cambiaron; usa /resume",
        "Die Suchergebnisse wurden geändert; nutze /resume", "Les résultats de recherche ont changé ; utilisez /resume",
        "Os resultados da pesquisa mudaram; use /resume",
    ),
    "Waiting for a new search query": (
        "Ожидается новый поисковый запрос", "Esperando una nueva búsqueda", "Neue Suchanfrage erwartet",
        "En attente d’une nouvelle recherche", "Aguardando uma nova pesquisa",
    ),
    "Line {line}: insertion is unconfirmed. Check the playlist contents before retrying": (
        "Строка {line}: добавление не подтверждено. Проверьте плейлист перед повторной попыткой",
        "Línea {line}: inserción no confirmada. Revisa la lista antes de reintentar",
        "Zeile {line}: Hinzufügen unbestätigt. Prüfe den Playlist-Inhalt vor einem erneuten Versuch",
        "Ligne {line} : ajout non confirmé. Vérifiez le contenu de la playlist avant de réessayer",
        "Linha {line}: inclusão não confirmada. Verifique a playlist antes de tentar novamente",
    ),
    "The outcome of inserting line {line} is unknown; use /resume": (
        "Результат добавления строки {line} неизвестен; используйте /resume",
        "Se desconoce el resultado de insertar la línea {line}; usa /resume",
        "Das Ergebnis des Hinzufügens von Zeile {line} ist unbekannt; nutze /resume",
        "Le résultat de l’ajout de la ligne {line} est inconnu ; utilisez /resume",
        "O resultado da inclusão da linha {line} é desconhecido; use /resume",
    ),
    "Line {line}: YouTube rejected the recording ({status})": (
        "Строка {line}: YouTube отклонил запись ({status})", "Línea {line}: YouTube rechazó la grabación ({status})",
        "Zeile {line}: YouTube hat die Aufnahme abgelehnt ({status})", "Ligne {line} : YouTube a rejeté l’enregistrement ({status})",
        "Linha {line}: o YouTube rejeitou a gravação ({status})",
    ),
}

_SAVED_MESSAGES = (
    (re.compile(r"Line (?P<line>\d+): insertion is unconfirmed\. Check the playlist contents before retrying"),
     "Line {line}: insertion is unconfirmed. Check the playlist contents before retrying"),
    (re.compile(r"The outcome of inserting line (?P<line>\d+) is unknown; use /resume"),
     "The outcome of inserting line {line} is unknown; use /resume"),
    (re.compile(r"Line (?P<line>\d+): YouTube rejected the recording \((?P<status>\d+)\)"),
     "Line {line}: YouTube rejected the recording ({status})"),
)


def translate(language: str, text: str, /, **values) -> str:
    """Translate known UI text, falling back to English for unsupported locales."""
    variants = _CATALOG.get(text)
    if variants is not None and language in _TRANSLATION_LANGUAGES:
        text = variants[_TRANSLATION_LANGUAGES.index(language)]
    return text.format(**values) if values else text


def translate_saved_message(language: str, text: str) -> str:
    """Localize workflow diagnostics without modifying persisted draft data."""
    if text == "No draft. Send a list of songs":
        return translate(language, "No draft. Send a list of songs.")
    for pattern, source in _SAVED_MESSAGES:
        match = pattern.fullmatch(text)
        if match:
            return translate(language, source, **match.groupdict())
    return translate(language, text)
