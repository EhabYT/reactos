#ifdef __REACTOS__
#include "precomp.h"
#include "inet_ntop.c"
#else
/*
 * Wininet - Utility functions
 *
 * Copyright 1999 Corel Corporation
 * Copyright 2002 CodeWeavers Inc.
 *
 * Ulrich Czekalla
 * Aric Stewart
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA
 */

#include "ws2tcpip.h"

#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "windef.h"
#include "winbase.h"
#include "wininet.h"
#include "winnls.h"

#include "wine/debug.h"
#include "internet.h"
#endif /* defined(__REACTOS__) */

WINE_DEFAULT_DEBUG_CHANNEL(wininet);

#define TIME_STRING_LEN  30

time_t ConvertTimeString(LPCWSTR asctime)
{
    WCHAR tmpChar[TIME_STRING_LEN];
    WCHAR *tmpChar2;
    struct tm t;
    int timelen = lstrlenW(asctime);

    if(!timelen)
        return 0;

    /* FIXME: the atoiWs below rely on that tmpChar is \0 padded */
    memset( tmpChar, 0, sizeof(tmpChar) );
    lstrcpynW(tmpChar, asctime, TIME_STRING_LEN);

    /* Assert that the string is the expected length */
    if (lstrlenW(asctime) >= TIME_STRING_LEN) FIXME("\n");

    /* Convert a time such as 'Mon, 15 Nov 1999 16:09:35 GMT' into a SYSTEMTIME structure
     * We assume the time is in this format
     * and divide it into easy to swallow chunks
     */
    tmpChar[3]='\0';
    tmpChar[7]='\0';
    tmpChar[11]='\0';
    tmpChar[16]='\0';
    tmpChar[19]='\0';
    tmpChar[22]='\0';
    tmpChar[25]='\0';

    memset( &t, 0, sizeof(t) );
    t.tm_year = wcstol(tmpChar+12, NULL, 10) - 1900;
    t.tm_mday = wcstol(tmpChar+5, NULL, 10);
    t.tm_hour = wcstol(tmpChar+17, NULL, 10);
    t.tm_min = wcstol(tmpChar+20, NULL, 10);
    t.tm_sec = wcstol(tmpChar+23, NULL, 10);

    /* and month */
    tmpChar2 = tmpChar + 8;
    switch(tmpChar2[2])
    {
        case 'n':
            if(tmpChar2[1]=='a')
                t.tm_mon = 0;
            else
                t.tm_mon = 5;
            break;
        case 'b':
            t.tm_mon = 1;
            break;
        case 'r':
            if(tmpChar2[1]=='a')
                t.tm_mon = 2;
            else
                t.tm_mon = 3;
            break;
        case 'y':
            t.tm_mon = 4;
            break;
        case 'l':
            t.tm_mon = 6;
            break;
        case 'g':
            t.tm_mon = 7;
            break;
        case 'p':
            t.tm_mon = 8;
            break;
        case 't':
            t.tm_mon = 9;
            break;
        case 'v':
            t.tm_mon = 10;
            break;
        case 'c':
            t.tm_mon = 11;
            break;
        default:
            FIXME("\n");
    }

    return mktime(&t);
}


server_addr_t *GetAddress(const WCHAR *name, INTERNET_PORT port)
{
    server_addr_t *server_addr, *p;
    struct sockaddr_storage *addr;
    ADDRINFOW *res, *ai, hints;
    unsigned int len, count;
    int ret;

    TRACE("%s\n", debugstr_w(name));

    memset( &hints, 0, sizeof(hints) );
    hints.ai_socktype = SOCK_STREAM;
    ret = GetAddrInfoW(name, NULL, &hints, &res);
    if (ret != 0)
    {
        TRACE("failed to get address of %s\n", debugstr_w(name));
        return NULL;
    }
    count = 0;
    for (ai = res; ai; ai = ai->ai_next)
        ++count;
    p = server_addr = heap_calloc(count, sizeof(*server_addr));
    ai = res;
    while (ai)
    {
        addr = &p->addr;
        p->addr_len = ai->ai_addrlen;
        memcpy( addr, ai->ai_addr, ai->ai_addrlen );
        /* Copy port */
        switch (ai->ai_family)
        {
        case AF_INET:
            ((struct sockaddr_in *)addr)->sin_port = htons(port);
            inet_ntop(ai->ai_family, &((struct sockaddr_in *)addr)->sin_addr, p->addr_str, INET6_ADDRSTRLEN);
            break;
        case AF_INET6:
            ((struct sockaddr_in6 *)addr)->sin6_port = htons(port);
            p->addr_str[0] = '[';
            inet_ntop(ai->ai_family, &((struct sockaddr_in6 *)addr)->sin6_addr, p->addr_str + 1, INET6_ADDRSTRLEN - 2);
            len = strlen(p->addr_str);
            p->addr_str[len] = ']';
            p->addr_str[len + 1] = 0;
            break;
        }
        if (!(ai = ai->ai_next))
            break;
        p->next = p + 1;
        p = p->next;
    }

    FreeAddrInfoW(res);
    return server_addr;
}

static int try_create_connect_socket(server_addr_t *addr, int af, DWORD timeout, object_header_t *hdr,
                                     DWORD_PTR callback_context)
{
    TIMEVAL timeout_timeval = {0, timeout * 1000};
    ULONG blocking;
    socklen_t len;
    FD_SET set;
    DWORD err;
    int res;
    int s;

    if (hdr)
        INTERNET_SendCallback(hdr, callback_context, INTERNET_STATUS_CONNECTING_TO_SERVER,
                              addr->addr_str, strlen(addr->addr_str) + 1);

    if (af != AF_UNSPEC && addr->addr.ss_family != af)
        return -1;

    if ((s = socket(addr->addr.ss_family, SOCK_STREAM, 0)) == -1)
        return -1;

    blocking = 0;
    ioctlsocket(s, FIONBIO, &blocking);

    if (!connect(s, (struct sockaddr *)&addr->addr, addr->addr_len))
        goto done;

    err = WSAGetLastError();
    if (err != WSAEINPROGRESS && err != WSAEWOULDBLOCK)
    {
        closesocket(s);
        return -1;
    }

    FD_ZERO(&set);
    FD_SET(s, &set);
    res = select(s + 1, NULL, &set, NULL, timeout == INFINITE ? NULL : &timeout_timeval);
    len = sizeof(res);
    if(!res || res == SOCKET_ERROR || getsockopt(s, SOL_SOCKET, SO_ERROR, (void *)&res, &len) || res)
    {
        closesocket(s);
        return -1;
    }

done:
    blocking = 1;
    ioctlsocket(s, FIONBIO, &blocking);
    if (hdr)
        INTERNET_SendCallback(hdr, callback_context, INTERNET_STATUS_CONNECTED_TO_SERVER,
                              addr->addr_str, strlen(addr->addr_str) + 1);
    return s;
}

int create_connect_socket(server_addr_t *addr, int af, DWORD timeout, object_header_t *hdr, DWORD_PTR callback_context)
{
    LARGE_INTEGER qpf, qpc, end;
    server_addr_t *a, tmp;
    int s;

    if (timeout != INFINITE)
    {
        QueryPerformanceFrequency(&qpf);
        QueryPerformanceCounter(&end);
        end.QuadPart += qpf.QuadPart / 1000 * timeout;
    }
    a = addr;
    while (a)
    {
        if ((s = try_create_connect_socket(a, af, timeout, hdr, callback_context)) != -1)
        {
            /* try this address first next time. */
            tmp = *addr;
            *addr = *a;
            *a = tmp;
            a->next = addr->next;
            addr->next = tmp.next;
            return s;
        }
        if (timeout != INFINITE)
        {
            QueryPerformanceCounter(&qpc);
            if (qpc.QuadPart >= end.QuadPart)
                return -1;
            timeout = (end.QuadPart - qpc.QuadPart) * 1000 / qpf.QuadPart;
        }
        a = a->next;
    }
    return -1;
}

/*
 * Helper function for sending async Callbacks
 */

static const char *get_callback_name(DWORD dwInternetStatus) {
    static const wininet_flag_info internet_status[] = {
#define FE(x) { x, #x }
	FE(INTERNET_STATUS_RESOLVING_NAME),
	FE(INTERNET_STATUS_NAME_RESOLVED),
	FE(INTERNET_STATUS_CONNECTING_TO_SERVER),
	FE(INTERNET_STATUS_CONNECTED_TO_SERVER),
	FE(INTERNET_STATUS_SENDING_REQUEST),
	FE(INTERNET_STATUS_REQUEST_SENT),
	FE(INTERNET_STATUS_RECEIVING_RESPONSE),
	FE(INTERNET_STATUS_RESPONSE_RECEIVED),
	FE(INTERNET_STATUS_CTL_RESPONSE_RECEIVED),
	FE(INTERNET_STATUS_PREFETCH),
	FE(INTERNET_STATUS_CLOSING_CONNECTION),
	FE(INTERNET_STATUS_CONNECTION_CLOSED),
	FE(INTERNET_STATUS_HANDLE_CREATED),
	FE(INTERNET_STATUS_HANDLE_CLOSING),
	FE(INTERNET_STATUS_REQUEST_COMPLETE),
	FE(INTERNET_STATUS_REDIRECT),
	FE(INTERNET_STATUS_INTERMEDIATE_RESPONSE),
	FE(INTERNET_STATUS_USER_INPUT_REQUIRED),
	FE(INTERNET_STATUS_STATE_CHANGE),
	FE(INTERNET_STATUS_COOKIE_SENT),
	FE(INTERNET_STATUS_COOKIE_RECEIVED),
	FE(INTERNET_STATUS_PRIVACY_IMPACTED),
	FE(INTERNET_STATUS_P3P_HEADER),
	FE(INTERNET_STATUS_P3P_POLICYREF),
	FE(INTERNET_STATUS_COOKIE_HISTORY)
#undef FE
    };
    DWORD i;

    for (i = 0; i < ARRAY_SIZE(internet_status); i++) {
	if (internet_status[i].val == dwInternetStatus) return internet_status[i].name;
    }
    return "Unknown";
}

static const char *debugstr_status_info(DWORD status, void *info)
{
    switch(status) {
    case INTERNET_STATUS_REQUEST_COMPLETE: {
        INTERNET_ASYNC_RESULT *iar = info;
        return wine_dbg_sprintf("{%s, %d}", wine_dbgstr_longlong(iar->dwResult), iar->dwError);
    }
    default:
        return wine_dbg_sprintf("%p", info);
    }
}

void INTERNET_SendCallback(object_header_t *hdr, DWORD_PTR context, DWORD status, void *info, DWORD info_len)
{
    void *new_info = info;

    if( !hdr->lpfnStatusCB )
        return;

    /* the IE5 version of wininet does not
       send callbacks if dwContext is zero */
    if(!context)
        return;

    switch(status) {
    case INTERNET_STATUS_NAME_RESOLVED:
    case INTERNET_STATUS_CONNECTING_TO_SERVER:
    case INTERNET_STATUS_CONNECTED_TO_SERVER:
        new_info = heap_alloc(info_len);
        if(new_info)
            memcpy(new_info, info, info_len);
        break;
    case INTERNET_STATUS_RESOLVING_NAME:
    case INTERNET_STATUS_REDIRECT:
        if(hdr->dwInternalFlags & INET_CALLBACKW) {
            new_info = heap_strdupW(info);
            break;
        }else {
            new_info = heap_strdupWtoA(info);
            info_len = strlen(new_info)+1;
            break;
        }
    }

    TRACE(" callback(%p) (%p (%p), %08lx, %d (%s), %s, %d)\n",
	  hdr->lpfnStatusCB, hdr->hInternet, hdr, context, status, get_callback_name(status),
	  debugstr_status_info(status, new_info), info_len);

    hdr->lpfnStatusCB(hdr->hInternet, context, status, new_info, info_len);

    TRACE(" end callback().\n");

    if(new_info != info)
        heap_free(new_info);
}
