/* SmartReco auth form validation.
 * Adds inline, accessible validation to forms marked [data-auth-form]:
 * - required fields are enforced and marked with an error message
 * - optional fields (no `required`) are only checked when filled
 * - fields are validated on blur, re-validated while typing (once an error
 *   is present), and all fields are checked on submit (focus first error).
 * Native validation bubbles are suppressed (form has novalidate); server-side
 * validation remains the fallback for no-JS clients.
 */
(function () {
  "use strict";

  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var MOBILE_RE = /^\+?[\d\s()-]{7,20}$/;

  function check(field) {
    var input = field.querySelector("input, select");
    if (!input) return "";
    var value = input.value.trim();
    if (!value) return input.required ? "This field is required." : "";

    if (input.type === "email") {
      return EMAIL_RE.test(value) ? "" : "Enter a valid email address.";
    }
    if (input.type === "tel") {
      return MOBILE_RE.test(value) ? "" : "Enter a valid mobile number (7–15 digits).";
    }
    if (input.name === "age") {
      var n = Number(value);
      if (!Number.isInteger(n) || n < 1 || n > 119) return "Enter an age between 1 and 119.";
    }
    if (input.name === "password" && value.length < 8) {
      return "Password must be at least 8 characters.";
    }
    if (input.name === "full_name" && value.length < 2) {
      return "Please enter your full name.";
    }
    if (input.tagName === "SELECT") {
      return value ? "" : "Please select an option.";
    }
    return "";
  }

  function setState(field, message) {
    var input = field.querySelector("input, select");
    var error = field.querySelector(".auth-field-error");
    if (message) {
      field.classList.add("has-error");
      if (input) input.setAttribute("aria-invalid", "true");
      if (error) error.textContent = message;
    } else {
      field.classList.remove("has-error");
      if (input) input.removeAttribute("aria-invalid");
      if (error) error.textContent = "";
    }
  }

  document.querySelectorAll("[data-auth-form]").forEach(function (form) {
    var fields = Array.prototype.slice.call(form.querySelectorAll("[data-field]"));

    fields.forEach(function (field) {
      var input = field.querySelector("input, select");
      if (!input) return;
      input.addEventListener("blur", function () { setState(field, check(field)); });
      input.addEventListener("input", function () {
        if (field.classList.contains("has-error")) setState(field, check(field));
      });
    });

    form.addEventListener("submit", function (e) {
      var ok = true;
      var first = null;
      fields.forEach(function (field) {
        var msg = check(field);
        setState(field, msg);
        if (msg) { ok = false; if (!first) first = field; }
      });
      if (!ok) {
        e.preventDefault();
        var el = first.querySelector("input, select");
        if (el) el.focus();
      }
    });
  });
})();
