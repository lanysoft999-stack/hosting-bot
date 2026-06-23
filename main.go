// main.go - Ohoster Hosting Bot (Go версия)
package main

import (
	"archive/zip"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	tgbotapi "github.com/go-telegram-bot-api/telegram-bot-api/v5"
	"github.com/google/uuid"
)

// Конфигурация
const (
	Token        = "1456462948:AAH1wfMw5sxS9p4niC3yjoxO-ndhD3xC1gY"
	AdminID      = 314148464
	Port         = "10000"
	FreeScripts  = 3
	FreeSizeMB   = 5
)

var (
	bot       *tgbotapi.BotAPI
	db        *sql.DB
	botActive = true
	waiting   = make(map[int64]bool)
	broadcast = make(map[int64]bool)
	adminAct  = make(map[int64]string)
)

// Структуры
type Script struct {
	ID        string
	UserID    int64
	Name      string
	Path      string
	Status    string
	Size      int64
	CreatedAt string
}

func main() {
	// Инициализация БД
	initDB()
	defer db.Close()

	// Запуск бота
	var err error
	bot, err = tgbotapi.NewBotAPI(Token)
	if err != nil {
		log.Fatal(err)
	}

	// Удаляем вебхук
	bot.Request(tgbotapi.DeleteWebhookConfig{})
	time.Sleep(2 * time.Second)

	log.Printf("🚀 Ohoster Bot запущен на порту %s", Port)

	// Запуск веб-сервера
	go startWebServer()

	// Канал для обновлений
	u := tgbotapi.NewUpdate(0)
	u.Timeout = 60
	updates := bot.GetUpdatesChan(u)

	// Обработка обновлений
	for update := range updates {
		if update.Message != nil {
			handleMessage(update.Message)
		} else if update.CallbackQuery != nil {
			handleCallback(update.CallbackQuery)
		}
	}
}

// ========== БД ==========
func initDB() {
	var err error
	db, err = sql.Open("sqlite3", "bot.db")
	if err != nil {
		log.Fatal(err)
	}

	db.Exec(`CREATE TABLE IF NOT EXISTS scripts (
		id TEXT, user_id INTEGER, name TEXT, path TEXT, status TEXT, size INTEGER, created_at TEXT)`)
	db.Exec(`CREATE TABLE IF NOT EXISTS users (
		user_id INTEGER PRIMARY KEY, username TEXT, joined_at TEXT)`)
	db.Exec(`CREATE TABLE IF NOT EXISTS banned (
		user_id INTEGER PRIMARY KEY)`)
}

func getScripts(uid int64) []Script {
	rows, _ := db.Query("SELECT * FROM scripts WHERE user_id=? ORDER BY created_at DESC", uid)
	defer rows.Close()
	
	var scripts []Script
	for rows.Next() {
		var s Script
		rows.Scan(&s.ID, &s.UserID, &s.Name, &s.Path, &s.Status, &s.Size, &s.CreatedAt)
		scripts = append(scripts, s)
	}
	return scripts
}

func getAllScripts() []Script {
	rows, _ := db.Query("SELECT * FROM scripts ORDER BY created_at DESC")
	defer rows.Close()
	
	var scripts []Script
	for rows.Next() {
		var s Script
		rows.Scan(&s.ID, &s.UserID, &s.Name, &s.Path, &s.Status, &s.Size, &s.CreatedAt)
		scripts = append(scripts, s)
	}
	return scripts
}

func countScripts(uid int64) int {
	var count int
	db.QueryRow("SELECT COUNT(*) FROM scripts WHERE user_id=?", uid).Scan(&count)
	return count
}

func isBanned(uid int64) bool {
	var exists bool
	db.QueryRow("SELECT EXISTS(SELECT 1 FROM banned WHERE user_id=?)", uid).Scan(&exists)
	return exists
}

// ========== ВЕБ-СЕРВЕР ==========
func startWebServer() {
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("OK"))
	})
	http.HandleFunc("/ping", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("OK"))
	})
	
	log.Printf("🌐 Веб-сервер на порту %s", Port)
	http.ListenAndServe(":"+Port, nil)
}

// ========== ОБРАБОТЧИКИ ==========
func handleMessage(msg *tgbotapi.Message) {
	uid := msg.From.ID
	
	switch {
	case msg.IsCommand() && msg.Command() == "start":
		handleStart(msg)
	case msg.Text == "📤 Загрузить":
		handleUpload(msg)
	case msg.Text == "💻 Мои хосты":
		handleHosts(msg)
	case msg.Text == "👤 Профиль":
		handleProfile(msg)
	case msg.Text == "🆘 Помощь":
		handleHelp(msg)
	case msg.Document != nil:
		handleDocument(msg)
	case msg.Text == "👥 Пользователи" && uid == AdminID:
		handleAdminUsers(msg)
	case msg.Text == "📊 Статистика" && uid == AdminID:
		handleAdminStats(msg)
	case msg.Text == "📨 Рассылка" && uid == AdminID:
		broadcast[uid] = true
		bot.Send(tgbotapi.NewMessage(uid, "📨 Отправьте сообщение:"))
	case msg.Text == "📦 Все хосты" && uid == AdminID:
		handleAdminAllHosts(msg)
	case msg.Text == "📥 Файлы юзера" && uid == AdminID:
		adminAct[uid] = "get_files"
		bot.Send(tgbotapi.NewMessage(uid, "🆔 ID пользователя:"))
	case msg.Text == "🗑 Удалить хосты" && uid == AdminID:
		adminAct[uid] = "del_hosts"
		bot.Send(tgbotapi.NewMessage(uid, "🆔 ID пользователя:"))
	case msg.Text == "🚫 Забанить" && uid == AdminID:
		adminAct[uid] = "ban_user"
		bot.Send(tgbotapi.NewMessage(uid, "🆔 ID для бана:"))
	case msg.Text == "🟢 Разбанить" && uid == AdminID:
		adminAct[uid] = "unban_user"
		bot.Send(tgbotapi.NewMessage(uid, "🆔 ID для разбана:"))
	case msg.Text == "🛑 Стоп бот" && uid == AdminID:
		botActive = false
		bot.Send(tgbotapi.NewMessage(uid, "🔴 Бот остановлен!"))
	case msg.Text == "🟢 Старт бот" && uid == AdminID:
		botActive = true
		bot.Send(tgbotapi.NewMessage(uid, "🟢 Бот запущен!"))
	case msg.Text == "👤 Режим юзера" && uid == AdminID:
		bot.Send(tgbotapi.NewMessage(uid, "👤 Режим юзера", replyMarkup=userKeyboard()))
	default:
		// Рассылка
		if broadcast[uid] {
			delete(broadcast, uid)
			users, _ := db.Query("SELECT user_id FROM users")
			defer users.Close()
			sent := 0
			for users.Next() {
				var targetID int64
				users.Scan(&targetID)
				bot.Send(tgbotapi.NewMessage(targetID, "📢 Рассылка Ohoster\n\n"+msg.Text))
				sent++
				time.Sleep(30 * time.Millisecond)
			}
			bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("✅ %d", sent)))
			return
		}
		
		// Админ действия
		if action, ok := adminAct[uid]; ok {
			delete(adminAct, uid)
			targetID, _ := strconv.ParseInt(msg.Text, 10, 64)
			
			switch action {
			case "get_files":
				userDir := filepath.Join("user_files", strconv.FormatInt(targetID, 10))
				files, _ := filepath.Glob(filepath.Join(userDir, "*"))
				for _, f := range files {
					doc := tgbotapi.NewDocument(uid, tgbotapi.FilePath(f))
					bot.Send(doc)
				}
			case "del_hosts":
				scripts := getScripts(targetID)
				for _, s := range scripts {
					os.RemoveAll(s.Path)
				}
				db.Exec("DELETE FROM scripts WHERE user_id=?", targetID)
				bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("✅ Хосты user%d удалены!", targetID)))
			case "ban_user":
				db.Exec("INSERT OR REPLACE INTO banned VALUES (?)", targetID)
				bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("🚫 user%d забанен!", targetID)))
			case "unban_user":
				db.Exec("DELETE FROM banned WHERE user_id=?", targetID)
				bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("🟢 user%d разбанен!", targetID)))
			}
		}
	}
}

func handleStart(msg *tgbotapi.Message) {
	uid := msg.From.ID
	
	if isBanned(uid) && uid != AdminID {
		bot.Send(tgbotapi.NewMessage(uid, "🚫 ВЫ ЗАБАНЕНЫ!"))
		return
	}
	
	db.Exec("INSERT OR IGNORE INTO users VALUES (?,?,?)", uid, msg.From.UserName, time.Now().Format(time.RFC3339))
	
	if uid == AdminID {
		scripts := getAllScripts()
		running := 0
		for _, s := range scripts {
			if s.Status == "running" {
				running++
			}
		}
		var userCount int
		db.QueryRow("SELECT COUNT(*) FROM users").Scan(&userCount)
		
		text := fmt.Sprintf("👑 АДМИН Ohoster\n\n👥 %d | 📦 %d | 🟢 %d", userCount, len(scripts), running)
		bot.Send(tgbotapi.NewMessage(uid, text))
		return
	}
	
	scripts := getScripts(uid)
	running := 0
	for _, s := range scripts {
		if s.Status == "running" {
			running++
		}
	}
	uptime := 100
	if len(scripts) > 0 {
		uptime = running * 100 / len(scripts)
	}
	
	text := fmt.Sprintf("🚀 Добро пожаловать в Ohoster!\n\n✅ Аптайм: %d%%\n⏹ Упало: %d\n🟢 Запущено: %d", uptime, len(scripts)-running, running)
	bot.Send(tgbotapi.NewMessage(uid, text))
}

func handleUpload(msg *tgbotapi.Message) {
	uid := msg.From.ID
	
	if !botActive && uid != AdminID {
		bot.Send(tgbotapi.NewMessage(uid, "🔴 Бот на обслуживании!"))
		return
	}
	if isBanned(uid) {
		bot.Send(tgbotapi.NewMessage(uid, "🚫 Вы забанены!"))
		return
	}
	if countScripts(uid) >= FreeScripts {
		bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("❌ Лимит %d скриптов!", FreeScripts)))
		return
	}
	
	waiting[uid] = true
	bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("📤 Отправьте .py или .zip (до %dМБ)", FreeSizeMB)))
}

func handleDocument(msg *tgbotapi.Message) {
	uid := msg.From.ID
	
	if !waiting[uid] {
		return
	}
	delete(waiting, uid)
	
	doc := msg.Document
	fn := doc.FileName
	fs := doc.FileSize
	
	if !strings.HasSuffix(fn, ".py") && !strings.HasSuffix(fn, ".zip") {
		bot.Send(tgbotapi.NewMessage(uid, "❌ .py или .zip!"))
		return
	}
	if fs > int64(FreeSizeMB*1024*1024) {
		bot.Send(tgbotapi.NewMessage(uid, fmt.Sprintf("❌ Макс %dМБ!", FreeSizeMB)))
		return
	}
	
	msg2 := tgbotapi.NewMessage(uid, "📥 Загрузка...")
	sent, _ := bot.Send(msg2)
	
	// Скачиваем файл
	fileConfig := tgbotapi.FileConfig{FileID: doc.FileID}
	file, err := bot.GetFile(fileConfig)
	if err != nil {
		bot.Send(tgbotapi.NewEditMessageText(uid, sent.MessageID, "❌ Ошибка скачивания!"))
		return
	}
	
	// Создаем папки
	sid := uuid.New().String()[:8]
	scriptDir := filepath.Join("scripts", strconv.FormatInt(uid, 10), sid)
	os.MkdirAll(scriptDir, 0755)
	
	// Скачиваем через HTTP
	fileURL := fmt.Sprintf("https://api.telegram.org/file/bot%s/%s", Token, file.FilePath)
	resp, _ := http.Get(fileURL)
	defer resp.Body.Close()
	
	tmpDir := filepath.Join("temp", strconv.FormatInt(uid, 10), uuid.New().String()[:8])
	os.MkdirAll(tmpDir, 0755)
	tmpFile := filepath.Join(tmpDir, fn)
	
	f, _ := os.Create(tmpFile)
	io.Copy(f, resp.Body)
	f.Close()
	
	if strings.HasSuffix(fn, ".zip") {
		// Распаковка
		archive, _ := zip.OpenReader(tmpFile)
		for _, zf := range archive.File {
			path := filepath.Join(scriptDir, zf.Name)
			if zf.FileInfo().IsDir() {
				os.MkdirAll(path, 0755)
			} else {
				os.MkdirAll(filepath.Dir(path), 0755)
				outFile, _ := os.Create(path)
				rc, _ := zf.Open()
				io.Copy(outFile, rc)
				outFile.Close()
				rc.Close()
			}
		}
		archive.Close()
	} else {
		copyFile(tmpFile, filepath.Join(scriptDir, fn))
	}
	
	// Запускаем
	bot.Send(tgbotapi.NewEditMessageText(uid, sent.MessageID, "⚡ Запуск..."))
	
	pid := runScript(scriptDir)
	
	if pid > 0 {
		var size int64
		filepath.Walk(scriptDir, func(path string, info os.FileInfo, err error) error {
			size += info.Size()
			return nil
		})
		
		db.Exec("INSERT INTO scripts VALUES (?,?,?,?,?,?,?)",
			sid, uid, fn, scriptDir, "running", size, time.Now().Format(time.RFC3339))
		
		keyboard := tgbotapi.NewInlineKeyboardMarkup(
			tgbotapi.NewInlineKeyboardRow(
				tgbotapi.NewInlineKeyboardButtonData("⏹ Стоп", "stop:"+sid),
				tgbotapi.NewInlineKeyboardButtonData("🗑 Удалить", "del:"+sid),
			),
		)
		
		text := fmt.Sprintf("✅ Запущен!\n📄 %s\n🆔 %s\nPID: %d", fn, sid, pid)
		edit := tgbotapi.NewEditMessageTextAndMarkup(uid, sent.MessageID, text, keyboard)
		bot.Send(edit)
	} else {
		bot.Send(tgbotapi.NewEditMessageText(uid, sent.MessageID, "❌ Ошибка запуска!"))
		os.RemoveAll(scriptDir)
	}
	
	os.RemoveAll(tmpDir)
}

func handleHosts(msg *tgbotapi.Message) {
	uid := msg.From.ID
	scripts := getScripts(uid)
	
	if len(scripts) == 0 {
		keyboard := tgbotapi.NewInlineKeyboardMarkup(
			tgbotapi.NewInlineKeyboardRow(
				tgbotapi.NewInlineKeyboardButtonData("📤 Загрузить скрипт", "upload_btn"),
			),
		)
		bot.Send(tgbotapi.NewMessage(uid, "😔 Нет сервисов"))
		bot.Send(tgbotapi.NewMessage(uid, "📤 Загрузите скрипт!", keyboard))
		return
	}
	
	running := 0
	for _, s := range scripts {
		if s.Status == "running" {
			running++
		}
	}
	uptime := running * 100 / len(scripts)
	
	text := fmt.Sprintf("💻 МОИ СЕРВИСЫ\n\n🟢 %d | 🔴 %d | 📈 %d%%\n\n", running, len(scripts)-running, uptime)
	
	var rows [][]tgbotapi.InlineKeyboardButton
	for i, s := range scripts {
		status := "🔴"
		if s.Status == "running" {
			status = "🟢"
		}
		text += fmt.Sprintf("%s %s | %.1fМБ | %s\n", status, s.Name, float64(s.Size)/1024/1024, s.ID)
		
		btn1 := fmt.Sprintf("⏹ Стоп %d", i+1)
		if s.Status != "running" {
			btn1 = fmt.Sprintf("▶️ Старт %d", i+1)
		}
		rows = append(rows, tgbotapi.NewInlineKeyboardRow(
			tgbotapi.NewInlineKeyboardButtonData(btn1, "stop:"+s.ID),
			tgbotapi.NewInlineKeyboardButtonData(fmt.Sprintf("🗑 Удалить %d", i+1), "del:"+s.ID),
		))
	}
	rows = append(rows, tgbotapi.NewInlineKeyboardRow(
		tgbotapi.NewInlineKeyboardButtonData("📤 Загрузить ещё", "upload_btn"),
	))
	
	bot.Send(tgbotapi.NewMessage(uid, text, tgbotapi.NewInlineKeyboardMarkup(rows...)))
}

func handleProfile(msg *tgbotapi.Message) {
	uid := msg.From.ID
	count := countScripts(uid)
	running := 0
	for _, s := range getScripts(uid) {
		if s.Status == "running" {
			running++
		}
	}
	
	text := fmt.Sprintf("👤 ПРОФИЛЬ\n\n🆔 %d\n📦 %d/%d\n🟢 %d", uid, count, FreeScripts, running)
	bot.Send(tgbotapi.NewMessage(uid, text))
}

func handleHelp(msg *tgbotapi.Message) {
	text := fmt.Sprintf("🆘 ПОМОЩЬ\n\n📤 Загрузить - .py/.zip\n💻 Мои хосты - управление\n👤 Профиль - статистика\n\n📦 Лимит: %d скриптов\n📊 До %dМБ", FreeScripts, FreeSizeMB)
	bot.Send(tgbotapi.NewMessage(msg.Chat.ID, text))
}

func handleAdminUsers(msg *tgbotapi.Message) {
	rows, _ := db.Query("SELECT user_id, username FROM users LIMIT 20")
	defer rows.Close()
	
	text := "👥 ПОЛЬЗОВАТЕЛИ\n\n"
	for rows.Next() {
		var uid int64
		var username string
		rows.Scan(&uid, &username)
		text += fmt.Sprintf("🆔 %d | @%s\n", uid, username)
	}
	bot.Send(tgbotapi.NewMessage(msg.Chat.ID, text))
}

func handleAdminStats(msg *tgbotapi.Message) {
	var users int
	db.QueryRow("SELECT COUNT(*) FROM users").Scan(&users)
	
	scripts := getAllScripts()
	running := 0
	var totalSize int64
	for _, s := range scripts {
		if s.Status == "running" {
			running++
		}
		totalSize += s.Size
	}
	
	text := fmt.Sprintf("📊 СТАТИСТИКА\n\n👥 %d\n📦 %d (🟢%d)\n💾 %.1fМБ", users, len(scripts), running, float64(totalSize)/1024/1024)
	bot.Send(tgbotapi.NewMessage(msg.Chat.ID, text))
}

func handleAdminAllHosts(msg *tgbotapi.Message) {
	scripts := getAllScripts()
	if len(scripts) == 0 {
		bot.Send(tgbotapi.NewMessage(msg.Chat.ID, "Нет хостов"))
		return
	}
	
	text := fmt.Sprintf("📦 ВСЕ ХОСТЫ (%d)\n\n", len(scripts))
	for i, s := range scripts {
		if i >= 15 {
			text += "..."
			break
		}
		status := "🔴"
		if s.Status == "running" {
			status = "🟢"
		}
		text += fmt.Sprintf("%s %s | user%d\n%s\n\n", status, s.Name, s.UserID, s.ID)
	}
	bot.Send(tgbotapi.NewMessage(msg.Chat.ID, text))
}

func handleCallback(callback *tgbotapi.CallbackQuery) {
	uid := callback.From.ID
	data := callback.Data
	
	bot.Request(tgbotapi.NewCallback(callback.ID, ""))
	
	switch {
	case data == "upload_btn":
		handleUpload(&tgbotapi.Message{From: callback.From, Chat: &tgbotapi.Chat{ID: uid}})
	
	case strings.HasPrefix(data, "stop:"):
		sid := data[5:]
		for _, s := range getScripts(uid) {
			if s.ID == sid {
				if s.Status == "running" {
					db.Exec("UPDATE scripts SET status='stopped' WHERE id=?", sid)
				} else {
					pid := runScript(s.Path)
					if pid > 0 {
						db.Exec("UPDATE scripts SET status='running' WHERE id=?", sid)
					}
				}
				break
			}
		}
		updateHostsMessage(uid, callback.Message.MessageID)
	
	case strings.HasPrefix(data, "del:"):
		sid := data[4:]
		for _, s := range getScripts(uid) {
			if s.ID == sid {
				db.Exec("DELETE FROM scripts WHERE id=?", sid)
				os.RemoveAll(s.Path)
				break
			}
		}
		updateHostsMessage(uid, callback.Message.MessageID)
	}
}

func updateHostsMessage(uid int64, msgID int) {
	scripts := getScripts(uid)
	
	if len(scripts) == 0 {
		keyboard := tgbotapi.NewInlineKeyboardMarkup(
			tgbotapi.NewInlineKeyboardRow(
				tgbotapi.NewInlineKeyboardButtonData("📤 Загрузить скрипт", "upload_btn"),
			),
		)
		edit := tgbotapi.NewEditMessageTextAndMarkup(uid, msgID, "😔 Нет сервисов", keyboard)
		bot.Send(edit)
		return
	}
	
	running := 0
	for _, s := range scripts {
		if s.Status == "running" {
			running++
		}
	}
	uptime := running * 100 / len(scripts)
	
	text := fmt.Sprintf("💻 МОИ СЕРВИСЫ\n\n🟢 %d | 🔴 %d | 📈 %d%%\n\n", running, len(scripts)-running, uptime)
	
	var rows [][]tgbotapi.InlineKeyboardButton
	for i, s := range scripts {
		status := "🔴"
		if s.Status == "running" {
			status = "🟢"
		}
		text += fmt.Sprintf("%s %s | %.1fМБ | %s\n", status, s.Name, float64(s.Size)/1024/1024, s.ID)
		
		btn1 := fmt.Sprintf("⏹ Стоп %d", i+1)
		if s.Status != "running" {
			btn1 = fmt.Sprintf("▶️ Старт %d", i+1)
		}
		rows = append(rows, tgbotapi.NewInlineKeyboardRow(
			tgbotapi.NewInlineKeyboardButtonData(btn1, "stop:"+s.ID),
			tgbotapi.NewInlineKeyboardButtonData(fmt.Sprintf("🗑 Удалить %d", i+1), "del:"+s.ID),
		))
	}
	rows = append(rows, tgbotapi.NewInlineKeyboardRow(
		tgbotapi.NewInlineKeyboardButtonData("📤 Загрузить ещё", "upload_btn"),
	))
	
	edit := tgbotapi.NewEditMessageTextAndMarkup(uid, msgID, text, tgbotapi.NewInlineKeyboardMarkup(rows...))
	bot.Send(edit)
}

// ========== ЗАПУСК СКРИПТА ==========
func runScript(path string) int {
	// Ищем main.py
	var mainFile string
	filepath.Walk(path, func(p string, info os.FileInfo, err error) error {
		if strings.HasSuffix(p, ".py") {
			if filepath.Base(p) == "main.py" {
				mainFile = p
				return filepath.SkipDir
			}
			if mainFile == "" {
				mainFile = p
			}
		}
		return nil
	})
	
	if mainFile == "" {
		return 0
	}
	
	cmd := exec.Command("python3", mainFile)
	cmd.Dir = path
	cmd.Stdout = nil
	cmd.Stderr = nil
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	
	err := cmd.Start()
	if err != nil {
		cmd = exec.Command("python", mainFile)
		cmd.Dir = path
		cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
		err = cmd.Start()
		if err != nil {
			return 0
		}
	}
	
	return cmd.Process.Pid
}

// ========== КЛАВИАТУРЫ ==========
func userKeyboard() tgbotapi.ReplyKeyboardMarkup {
	return tgbotapi.NewReplyKeyboard(
		tgbotapi.NewKeyboardButtonRow(
			tgbotapi.NewKeyboardButton("📤 Загрузить"),
			tgbotapi.NewKeyboardButton("💻 Мои хосты"),
		),
		tgbotapi.NewKeyboardButtonRow(
			tgbotapi.NewKeyboardButton("👤 Профиль"),
			tgbotapi.NewKeyboardButton("🆘 Помощь"),
		),
	)
}

// ========== УТИЛИТЫ ==========
func copyFile(src, dst string) {
	source, _ := os.Open(src)
	defer source.Close()
	destination, _ := os.Create(dst)
	defer destination.Close()
	io.Copy(destination, source)
}
