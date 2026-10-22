const modal = document.getElementById("modal");
const form = document.getElementById("applicationForm");
const statusText = document.getElementById("formStatus");

function openModal() {
    modal.classList.add("active");
    document.body.style.overflow = "hidden";
}

function closeModal() {
    modal.classList.remove("active");
    document.body.style.overflow = "";
}

modal.addEventListener("click", (event) => {
    if (event.target === modal) {
        closeModal();
    }
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
        closeModal();
    }
});

form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const button = form.querySelector("button[type=submit]");

    button.disabled = true;
    button.textContent = "Отправляем...";
    statusText.textContent = "";

    const formData = new FormData(form);

    const data = Object.fromEntries(formData.entries());

    data.consent = formData.has("consent");

    try {
        const response = await fetch("/api/apply", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Ошибка отправки");
        }

        statusText.style.color = "#d4f78c";
        statusText.textContent = "✅ Заявка успешно отправлена!";

        form.reset();

        setTimeout(() => {
            closeModal();
            statusText.textContent = "";
        }, 2500);

    } catch (error) {
        statusText.style.color = "#ff8585";
        statusText.textContent = "❌ " + error.message;
    } finally {
        button.disabled = false;
        button.textContent = "Отправить заявку ↗";
    }
});
