import { getRequestConfig } from "next-intl/server";
import { cookies } from "next/headers";
import { defaultLocale, isLocale, LOCALE_COOKIE } from "./config";

export default getRequestConfig(async () => {
  const fromCookie = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale = isLocale(fromCookie) ? fromCookie : defaultLocale;
  // `now` is the request time: relative times ("5 minutes ago") are computed against it on server and client alike
  return { locale, now: new Date(), messages: (await import(`../../messages/${locale}.json`)).default };
});
