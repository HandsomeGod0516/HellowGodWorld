import {
    createContext,
    type ReactNode,
    useContext,
    useEffect,
    useState,
} from "react";
import { jwtDecode, type JwtPayload } from "jwt-decode";
import Sdk from "casdoor-js-sdk";
import type { SdkConfig } from "casdoor-js-sdk/lib/esm/sdk";

export const sdkConfig = {
    serverUrl: "https://login.fiblab.net",
    clientId: "7ffcbfe4ae0fcb2c0d63",
    appName: "agentsociety",
    organizationName: "fiblab",
    redirectPath: "/callback",
    signinPath: "/api/signin",
};

export const DEMO_USER_TOKEN = "DEMO_USER_TOKEN";

let casdoorSdk: Sdk | undefined;

/** 獲取全域性的 Casdoor SDK，如果還沒有初始化，就用 `config` 新建一個。*/
export function getCasdoorSdk(config: SdkConfig) {
    return (casdoorSdk ??= new Sdk(config));
}

/** 獲取全域性的 access token。未登入時返回 `null`。*/
export function getAccessToken() {
    const token = localStorage.getItem("access_token");
    if (token === DEMO_USER_TOKEN) {
        return token;
    }
    // 檢查 token 是否過期
    if (token) {
        const decoded = jwtDecode<AccessTokenPayload>(token);
        if (decoded.exp < Date.now() / 1000) {
            localStorage.removeItem("access_token");
            return null;
        }
    }
    return token;
}

export interface AccessTokenPayload extends JwtPayload {
    /**
     * Token 的釋出者。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    iss: string;
    /**
     * Token 的主題。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    sub: string;
    /**
     * Token 的目標應用 ID。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    aud: string[];
    /**
     * 使用 Token 的應用 ID。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    azp: string;
    /**
     * Token 的唯一識別符號。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    jti: string;
    /** Token 的過期時間（Unix 時間戳）。*/
    exp: number;
    /** Token 的生效時間（Unix 時間戳）。*/
    nbf: number;
    /** Token 的簽發時間（Unix 時間戳）。*/
    iat: number;

    /** 使用者的登入名。*/
    name: string;
    /** 使用者的 UUID。*/
    id: string;
    /** 使用者的顯示名。*/
    displayName: string;
    /** 使用者的頭像 URL。*/
    avatar: string;
    /** 使用者的郵件地址。*/
    email: string;
    /** 使用者的手機號。*/
    phone: string;

    owner: "fiblab";
    tokenType: "access-token";
    scope: "profile";
}

/** 獲取全域性的 access token 並解碼。解碼得到的 payload 中也有一些使用者資訊，
 * 可以在一定程度上代替透過 API 獲取使用者資訊。未登入時返回 `null`。*/
export function getDecodedAccessToken() {
    const token = getAccessToken();
    if (token === null) return;
    return jwtDecode<AccessTokenPayload>(token);
}

export interface UserInfo {
    /**
     * Token 的釋出者。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    iss: string;
    /**
     * Token 的主題。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    sub: string;
    /**
     * Token 的目標應用 ID。見 [JWT 標準][1]。
     *
     * [1]: https://datatracker.ietf.org/doc/html/rfc7519#section-4.1
     */
    aud: string;
    /** 使用者的全名（顯示名）。*/
    name: string;
    /** 短使用者名稱（登入名）。*/
    prefered_username: string;
    /** 使用者的郵件地址。*/
    email?: string;
    /** 使用者的郵件地址是否已確認。*/
    email_verified?: boolean;
    /** 使用者的手機號。*/
    phone?: string;
    /** 使用者的頭像 URL。*/
    picture: string;
    /** 使用者的地址。*/
    address?: string;
    /** 使用者所屬的使用者組。*/
    groups: string[];
    /** 使用者的角色。*/
    roles: string[];
}

const AuthContext = createContext<UserInfo | undefined>(undefined);

/** 獲取使用者資訊。必須在開啟了 `onlineCheck` 的 {@linkcode AuthProvider} 內部使用，否則返回 `undefined`。*/
export function useUserInfo() {
    return useContext(AuthContext);
}

export interface AuthProviderProps {
    /**
     * Casdoor SDK 的配置。
     *
     * **注意**：初始化的 SDK 將儲存為全域性變數，因此一個專案內不可有多個不同的 SDK 配置。
     * 透過 {@linkcode getCasdoorSdk} 可以獲取到全域性的 SDK。
     */
    sdkConfig: SdkConfig;
    /**
     * 是否聯網檢查使用者登入狀態有效性。如果檢查，那麼在內部還可以使用 {@linkcode useUserInfo} 訪問到使用者資訊。
     * @default false
     */
    onlineCheck?: boolean;
    /**
     * 如果開啟了 `onlineCheck`，在檢查過程中時向使用者顯示的載入介面。
     * @default "Logging in……"
     */
    loading?: ReactNode;
    /** 使用者登入後顯示的內容。*/
    children: ReactNode;
    /**
     * 是否立即跳轉到登入頁面。
     * @default true
     */
    gotoLoginImmediately?: boolean;
}

/**
 * 確保只有已登入的使用者可以訪問到內部的內容，未登入的使用者將被重定向到登入介面。
 */
export function AuthProvider(props: AuthProviderProps): ReactNode {
    const sdk = getCasdoorSdk(props.sdkConfig);
    const token = getAccessToken();
    const [userInfo, setUserInfo] = useState<UserInfo>();
    useEffect(
        () => {
            if (token === null) {
                if (props.gotoLoginImmediately === true || props.gotoLoginImmediately === undefined) {
                    location.href = sdk.getSigninUrl();
                }
            } else {
                if (props.onlineCheck) {
                    sdk.getUserInfo(token).then((_resp) => {
                        // sdk 的型別宣告有誤
                        const resp = _resp as unknown as UserInfo | { status: "error" };
                        if ("status" in resp) {
                            location.href = sdk.getSigninUrl();
                        } else {
                            setUserInfo(resp);
                        }
                    });
                }
            }
        },
        [token, props.onlineCheck], // eslint-disable-line react-hooks/exhaustive-deps
    );
    if (props.gotoLoginImmediately === false) {
        return (
            <AuthContext.Provider value={userInfo}>
                {props.children}
            </AuthContext.Provider>
        );
    }
    return token === null ? (
        "Skipping login……"
    ) : props.onlineCheck && userInfo === undefined ? (
        (props.loading ?? "Logging in……")
    ) : (
        <AuthContext.Provider value= { userInfo } >
        { props.children }
    </AuthContext.Provider>
  );
}

export interface AuthCallbackProps {
    /**
     * Casdoor SDK 的配置。
     *
     * **注意**：初始化的 SDK 將儲存為全域性變數，因此一個專案內不可有多個不同的 SDK 配置。
     * 透過 {@linkcode getCasdoorSdk} 可以獲取到全域性的 SDK。
     */
    sdkConfig: SdkConfig;
    /** 登入的 API 源點，末尾不帶 `/`。*/
    signinOrigin: string;
    /**
     * 登陸的 API 路徑。
     * @default "/api/signin"
     */
    signinPath?: string;
    /**
     * 跳轉時的回撥。可以使用 React Router 的跳轉函式。
     * @default () => { location.href = "/"; }
     */
    onRedirect?: () => void;
    /**
     * 發生錯誤時顯示的內容。
     * @default (err) => err.toString()
     */
    error?: (err: unknown) => ReactNode;
    /**
     * 跳轉前顯示的內容。
     * @default "Skipping login……"
     */
    children?: ReactNode;
}

/** 登入的回撥頁面。*/
export function AuthCallback(props: AuthCallbackProps): ReactNode {
    const [error, setError] = useState<unknown>();
    useEffect(
        () => {
            getCasdoorSdk(props.sdkConfig)
                .signin(props.signinOrigin, props.signinPath)
                .then((_resp) => {
                    // sdk 的型別宣告有誤
                    const resp = _resp as unknown as { token?: string };
                    if (!resp.token) {
                        throw new Error(
                            "Login API returned an abnormal result: " + JSON.stringify(resp),
                        );
                    }
                    localStorage.setItem("access_token", resp.token);
                    if (props.onRedirect) {
                        props.onRedirect();
                    } else {
                        location.href = "/";
                    }
                })
                .catch((err) => setError(err));
        },
        [], // eslint-disable-line react-hooks/exhaustive-deps
    );
    return error
        ? props.error
            ? props.error(error)
            : (error as object).toString()
        : (props.children ?? "Skipping login……");
}
