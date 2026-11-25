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
    "Search again: /edit <source line> <query>": (
        "Искать заново: /edit <номер строки> <запрос>", "Buscar de nuevo: /edit <número de línea> <consulta>",
        "Erneut suchen: /edit <Zeilennummer> <Suchanfrage>", "Rechercher à nouveau : /edit <numéro de ligne> <requête>",
        "Pesquisar novamente: /edit <número da linha> <consulta>",
    ),
    "Choose the interface language": (
        "Выбрать язык интерфейса", "Elegir el idioma de la interfaz",
        "Sprache der Oberfläche wählen", "Choisir la langue de l’interface",
        "Escolher o idioma da interface",
    ),
    "Send a list with one song per line, for example Oasis — Wonderwall. Commands: /resume, /cancel, /edit <source line> <query>, /language. Use the Menu button to see all commands.": (
        "Отправьте список: одна песня на строку, например Oasis — Wonderwall. Команды: /resume, /cancel, /edit <номер строки> <запрос>, /language. Все команды доступны через кнопку меню.",
        "Envía una lista con una canción por línea, por ejemplo Oasis — Wonderwall. Comandos: /resume, /cancel, /edit <número de línea> <consulta>, /language. Usa el botón Menú para ver todos los comandos.",
        "Sende eine Liste mit einem Titel pro Zeile, zum Beispiel Oasis — Wonderwall. Befehle: /resume, /cancel, /edit <Zeilennummer> <Suchanfrage>, /language. Über die Menü-Schaltfläche siehst du alle Befehle.",
        "Envoyez une liste avec une chanson par ligne, par exemple Oasis — Wonderwall. Commandes : /resume, /cancel, /edit <numéro de ligne> <requête>, /language. Le bouton Menu affiche toutes les commandes.",
        "Envie uma lista com uma música por linha, por exemplo Oasis — Wonderwall. Comandos: /resume, /cancel, /edit <número da linha> <consulta>, /language. Use o botão Menu para ver todos os comandos.",
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
    " · album: {album}": (
        " · альбом: {album}", " · álbum: {album}", " · Album: {album}",
        " · album : {album}", " · álbum: {album}",
    ),
    " · artist and title order corrected": (
        " · порядок исполнителя и названия исправлен", " · se corrigió el orden de artista y título",
        " · Reihenfolge von Interpret und Titel korrigiert", " · ordre artiste/titre corrigé",
        " · ordem de artista e título corrigida",
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
    "Create public playlist": (
        "Создать публичный плейлист", "Crear lista pública", "Öffentliche Playlist erstellen",
        "Créer une playlist publique", "Criar playlist pública",
    ),
    "Cancel": ("Отмена", "Cancelar", "Abbrechen", "Annuler", "Cancelar"),
    "Reviewed {count} selected entries. Use /edit <source line> <search query> to search again for a song. Create a private or public playlist?": (
        "Проверено выбранных записей: {count}. Используйте /edit <номер строки> <поисковый запрос>, чтобы заново найти песню. Создать приватный или публичный плейлист?",
        "Se revisaron {count} entradas seleccionadas. Usa /edit <número de línea> <búsqueda> para buscar de nuevo una canción. ¿Crear una lista privada o pública?",
        "{count} ausgewählte Einträge geprüft. Nutze /edit <Zeilennummer> <Suchanfrage>, um einen Titel erneut zu suchen. Eine private oder öffentliche Playlist erstellen?",
        "{count} entrées sélectionnées vérifiées. Utilisez /edit <numéro de ligne> <recherche> pour relancer la recherche d’une chanson. Créer une playlist privée ou publique ?",
        "{count} entradas selecionadas revisadas. Use /edit <número da linha> <pesquisa> para pesquisar uma música novamente. Criar uma playlist privada ou pública?",
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
    "Use /edit <source line number> <search query> from the review": (
        "Используйте /edit <номер строки> <поисковый запрос> из ревью",
        "Usa /edit <número de línea> <búsqueda> desde la revisión", "Nutze /edit <Zeilennummer> <Suchanfrage> in der Übersicht",
        "Utilisez /edit <numéro de ligne> <recherche> à l’étape de vérification", "Use /edit <número da linha> <pesquisa> na revisão",
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
    "All songs have been skipped. Use /edit <source line> <search query> to search again or /cancel": (
        "Все песни пропущены. Используйте /edit <номер строки> <запрос>, чтобы искать снова, или /cancel",
        "Se omitieron todas las canciones. Usa /edit <número de línea> <búsqueda> para buscar de nuevo o /cancel",
        "Alle Titel wurden übersprungen. Nutze /edit <Zeilennummer> <Suchanfrage> für eine neue Suche oder /cancel",
        "Toutes les chansons ont été ignorées. Utilisez /edit <numéro de ligne> <recherche> pour relancer la recherche ou /cancel",
        "Todas as músicas foram ignoradas. Use /edit <número da linha> <pesquisa> para pesquisar novamente ou /cancel",
    ),
    "Gemini rejected the API key. Check GEMINI_API_KEY, restart the bot and use /resume": (
        "Gemini отклонил API-ключ. Проверьте GEMINI_API_KEY, перезапустите бота и используйте /resume",
        "Gemini rechazó la clave de API. Revisa GEMINI_API_KEY, reinicia el bot y usa /resume",
        "Gemini hat den API-Schlüssel abgelehnt. Prüfe GEMINI_API_KEY, starte den Bot neu und nutze /resume",
        "Gemini a refusé la clé d’API. Vérifiez GEMINI_API_KEY, redémarrez le bot et utilisez /resume",
        "O Gemini rejeitou a chave de API. Verifique GEMINI_API_KEY, reinicie o bot e use /resume",
    ),
    "Enter a search query between 1 and 200 characters long": (
        "Введите поисковый запрос длиной от 1 до 200 символов", "Introduce una búsqueda de entre 1 y 200 caracteres",
        "Gib eine Suchanfrage mit 1 bis 200 Zeichen ein", "Saisissez une recherche de 1 à 200 caractères",
        "Digite uma pesquisa com 1 a 200 caracteres",
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


_CATALOG.update({
    "YouTube quota or request limits have been reached. Check Google Cloud quotas and use /resume after the limit resets.": (
        "Исчерпана квота или лимит запросов YouTube. Проверьте квоты Google Cloud и используйте /resume после сброса лимита.",
        "Se alcanzó la cuota o el límite de solicitudes de YouTube. Consulta las cuotas de Google Cloud y usa /resume cuando se restablezca el límite.",
        "Das YouTube-Kontingent oder Anfragelimit ist erreicht. Prüfe die Google-Cloud-Kontingente und nutze /resume nach dem Zurücksetzen des Limits.",
        "Le quota ou la limite de requêtes YouTube est atteint. Vérifiez les quotas Google Cloud et utilisez /resume après leur réinitialisation.",
        "A cota ou o limite de solicitações do YouTube foi atingido. Verifique as cotas do Google Cloud e use /resume após a redefinição do limite.",
    ),
    "Google authorization lacks the required YouTube permissions. Repeat authorization using the README instructions, then use /resume.": (
        "В авторизации Google отсутствуют необходимые права YouTube. Повторите авторизацию по инструкции README, затем используйте /resume.",
        "La autorización de Google carece de los permisos necesarios de YouTube. Repite la autorización siguiendo el README y usa /resume.",
        "Der Google-Autorisierung fehlen die erforderlichen YouTube-Berechtigungen. Wiederhole die Autorisierung gemäß README und nutze /resume.",
        "L’autorisation Google ne dispose pas des permissions YouTube requises. Refaites l’autorisation selon le README, puis utilisez /resume.",
        "A autorização do Google não tem as permissões necessárias do YouTube. Repita a autorização conforme o README e use /resume.",
    ),
    "Google authorization is missing or expired. Repeat authorization using the README instructions, then use /resume.": (
        "Авторизация Google отсутствует или истекла. Повторите авторизацию по инструкции README, затем используйте /resume.",
        "La autorización de Google falta o ha caducado. Repite la autorización siguiendo el README y usa /resume.",
        "Die Google-Autorisierung fehlt oder ist abgelaufen. Wiederhole die Autorisierung gemäß README und nutze /resume.",
        "L’autorisation Google est absente ou a expiré. Refaites l’autorisation selon le README, puis utilisez /resume.",
        "A autorização do Google está ausente ou expirou. Repita a autorização conforme o README e use /resume.",
    ),
    "An external service timed out. Check your connection and use /resume to reconcile the saved state.": (
        "Время ожидания внешнего сервиса истекло. Проверьте соединение и используйте /resume для сверки сохранённого состояния.",
        "Un servicio externo agotó el tiempo de espera. Comprueba tu conexión y usa /resume para conciliar el estado guardado.",
        "Ein externer Dienst hat das Zeitlimit überschritten. Prüfe die Verbindung und nutze /resume zum Abgleich des gespeicherten Zustands.",
        "Un service externe a dépassé le délai d’attente. Vérifiez la connexion et utilisez /resume pour vérifier l’état enregistré.",
        "Um serviço externo excedeu o tempo limite. Verifique sua conexão e use /resume para reconciliar o estado salvo.",
    ),
    "An external service could not be reached. Check your connection and use /resume to reconcile the saved state.": (
        "Внешний сервис недоступен. Проверьте соединение и используйте /resume для сверки сохранённого состояния.",
        "No se pudo contactar con un servicio externo. Comprueba tu conexión y usa /resume para conciliar el estado guardado.",
        "Ein externer Dienst ist nicht erreichbar. Prüfe die Verbindung und nutze /resume zum Abgleich des gespeicherten Zustands.",
        "Un service externe est inaccessible. Vérifiez la connexion et utilisez /resume pour vérifier l’état enregistré.",
        "Não foi possível acessar um serviço externo. Verifique sua conexão e use /resume para reconciliar o estado salvo.",
    ),
    "The creation request was not sent. Fix authorization or network issues and use /resume": (
        "Запрос на создание не отправлен. Исправьте авторизацию или проблемы сети и используйте /resume",
        "La solicitud de creación no se envió. Soluciona la autorización o la red y usa /resume",
        "Die Erstellungsanfrage wurde nicht gesendet. Behebe Autorisierungs- oder Netzwerkprobleme und nutze /resume",
        "La requête de création n’a pas été envoyée. Corrigez l’autorisation ou le réseau et utilisez /resume",
        "A solicitação de criação não foi enviada. Corrija a autorização ou a rede e use /resume",
    ),
    "An external service or internal operation failed. Check the bot logs for the error category.": (
        "Ошибка внешнего сервиса или внутренней операции. Категория ошибки записана в журнале бота.",
        "Falló un servicio externo o una operación interna. Consulta la categoría del error en el registro del bot.",
        "Ein externer Dienst oder interner Vorgang ist fehlgeschlagen. Die Fehlerkategorie steht im Bot-Protokoll.",
        "Un service externe ou une opération interne a échoué. Consultez la catégorie d’erreur dans le journal du bot.",
        "Um serviço externo ou uma operação interna falhou. Consulte a categoria do erro no registro do bot.",
    ),
    "Line {line}: duplicate recording skipped": (
        "Строка {line}: повторная запись пропущена", "Línea {line}: grabación duplicada omitida",
        "Zeile {line}: doppelte Aufnahme übersprungen", "Ligne {line} : enregistrement en double ignoré",
        "Linha {line}: gravação duplicada ignorada",
    ),
    "Songs are still being matched. Use /resume to continue or /cancel.": (
        "Песни ещё подбираются. Используйте /resume, чтобы продолжить, или /cancel.",
        "Las canciones aún se están buscando. Usa /resume para continuar o /cancel.",
        "Titel werden noch zugeordnet. Nutze /resume zum Fortsetzen oder /cancel.",
        "Les chansons sont encore en cours de correspondance. Utilisez /resume pour continuer ou /cancel.",
        "As músicas ainda estão sendo associadas. Use /resume para continuar ou /cancel.",
    ),
    "Line {line} ({artist} — {title}): no search results, skipped": (
        "Строка {line} ({artist} — {title}): ничего не найдено, пропущена",
        "Línea {line} ({artist} — {title}): sin resultados de búsqueda, omitida",
        "Zeile {line} ({artist} — {title}): keine Suchergebnisse, übersprungen",
        "Ligne {line} ({artist} — {title}) : aucun résultat de recherche, ignorée",
        "Linha {line} ({artist} — {title}): nenhum resultado da pesquisa, ignorada",
    ),
    "Line {line} ({artist} — {title}): no matching recording found, skipped": (
        "Строка {line} ({artist} — {title}): подходящая запись не найдена, пропущена",
        "Línea {line} ({artist} — {title}): no se encontró una grabación coincidente, omitida",
        "Zeile {line} ({artist} — {title}): keine passende Aufnahme gefunden, übersprungen",
        "Ligne {line} ({artist} — {title}) : aucun enregistrement correspondant trouvé, ignorée",
        "Linha {line} ({artist} — {title}): nenhuma gravação correspondente encontrada, ignorada",
    ),
    "Line {line} ({artist} — {title}): automatic selection failed, skipped": (
        "Строка {line} ({artist} — {title}): автоматический выбор не удался, пропущена",
        "Línea {line} ({artist} — {title}): la selección automática falló, omitida",
        "Zeile {line} ({artist} — {title}): automatische Auswahl fehlgeschlagen, übersprungen",
        "Ligne {line} ({artist} — {title}) : la sélection automatique a échoué, ignorée",
        "Linha {line} ({artist} — {title}): a seleção automática falhou, ignorada",
    ),
})

_SAVED_MESSAGES = (
    (re.compile(r"Line (?P<line>\d+): duplicate recording skipped"),
     "Line {line}: duplicate recording skipped"),
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


def safe_workflow_message(text: str) -> bool:
    """Only application-authored diagnostics may cross the error UI boundary."""
    return (text in _CATALOG or text == "No draft. Send a list of songs"
            or any(pattern.fullmatch(text) for pattern, _ in _SAVED_MESSAGES))
