from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, Field, ConfigDict


class SocialMediaPerson(BaseModel):
    """
    Social Media Person Model
    """

    model_config = ConfigDict(use_enum_values=True)

    id: int = Field(..., description="Person ID")
    username: str = Field(..., description="Username")
    bio: Optional[str] = Field(None, description="User biography")
    created_at: datetime = Field(default_factory=datetime.now, description="Account creation time")
    followers_count: int = Field(0, ge=0, description="Number of followers")
    following_count: int = Field(0, ge=0, description="Number of users being followed")
    posts_count: int = Field(0, ge=0, description="Number of posts created")
    camp_score: Optional[float] = Field(
        None,
        description="Camp score for polarization experiment, optional",
    )
    following: List[int] = Field(default_factory=list, description="IDs of users this user follows")
    post_ids: List[int] = Field(default_factory=list, description="IDs of posts by this person")
    comment_ids: List[int] = Field(default_factory=list, description="IDs of comments by this person")
    liked_post_ids: List[int] = Field(default_factory=list, description="IDs of posts liked by this person")

    def __str__(self) -> str:
        return f"User {self.username} (ID: {self.id}), Followers: {self.followers_count}, Following: {self.following_count}, Posts: {self.posts_count}"


class Post(BaseModel):
    """
    貼文模型(原創、轉發或評論)
    """

    model_config = ConfigDict(use_enum_values=True)

    post_id: int = Field(..., description="Post ID")
    author_id: int = Field(..., description="Author user ID")
    content: str = Field(..., min_length=1, max_length=5000, description="Post content")
    post_type: Literal["original", "repost", "comment"] = Field("original", description="Post type: original, repost, or comment")
    parent_id: Optional[int] = Field(None, description="Parent post ID (for repost and comment)")
    created_at: datetime = Field(default_factory=datetime.now, description="Post creation time")
    likes_count: int = Field(0, ge=0, description="Number of likes")
    reposts_count: int = Field(0, ge=0, description="Number of reposts")
    comments_count: int = Field(0, ge=0, description="Number of comments")
    view_count: int = Field(0, ge=0, description="Number of views")
    liked_by: List[int] = Field(default_factory=list, description="User IDs who liked this post")
    tags: List[str] = Field(default_factory=list, description="話題標籤列表，最多10個")
    topic_category: Optional[str] = Field(None, description="主要話題分類（politics/sports/tech等）")

    def __str__(self) -> str:
        return f"{self.post_type.capitalize()} Post (ID: {self.post_id}) by User {self.author_id}: {self.content[:50]}{'...' if len(self.content) > 50 else ''}, Likes: {self.likes_count}, Reposts: {self.reposts_count}, Comments: {self.comments_count}"


class Comment(BaseModel):
    """Comment Model"""

    model_config = ConfigDict(use_enum_values=True)

    comment_id: int = Field(..., description="Comment ID")
    post_id: int = Field(..., description="Post ID that this comment belongs to")
    author_id: int = Field(..., description="Commenter user ID")
    content: str = Field(..., min_length=1, max_length=2000, description="Comment content")
    created_at: datetime = Field(default_factory=datetime.now, description="Comment creation time")
    likes_count: int = Field(0, ge=0, description="Number of likes")

    def __str__(self) -> str:
        return f"Comment (ID: {self.comment_id}) by User {self.author_id}: {self.content[:30]}{'...' if len(self.content) > 30 else ''}"


__all__ = [
    "SocialMediaPerson",
    "Post",
    "Comment",
    # Response Models
    "CreatePostResponse",
    "LikePostResponse",
    "UnlikePostResponse",
    "FollowUserResponse",
    "UnfollowUserResponse",
    "ViewPostResponse",
    "CommentOnPostResponse",
    "RepostResponse",
    "RefreshFeedResponse",
    "SearchPostsResponse",
    "ObserveUserResponse",
]


# ============ Response Models ============

class CreatePostResponse(BaseModel):
    """建立帖子的響應"""
    post_id: int = Field(..., description="新建立的帖子ID")
    author_id: int = Field(..., description="作者ID")
    content: str = Field(..., description="帖子內容")
    tags: List[str] = Field(default_factory=list, description="話題標籤")
    created_at: str = Field(..., description="建立時間(ISO格式)")
    post_type: str = Field("original", description="帖子型別")


class LikePostResponse(BaseModel):
    """點贊帖子的響應"""
    post_id: int = Field(..., description="帖子ID")
    user_id: int = Field(..., description="點贊使用者ID")
    total_likes: int = Field(..., description="帖子當前總點贊數")


class UnlikePostResponse(BaseModel):
    """取消點讚的響應"""
    post_id: int = Field(..., description="帖子ID")
    user_id: int = Field(..., description="使用者ID")
    total_likes: int = Field(..., description="帖子當前總點贊數")


class FollowUserResponse(BaseModel):
    """關注使用者的響應"""
    follower_id: int = Field(..., description="關注者ID")
    followee_id: int = Field(..., description="被關注者ID")
    follower_following_count: int = Field(..., description="關注者的關注數")
    followee_followers_count: int = Field(..., description="被關注者的粉絲數")


class UnfollowUserResponse(BaseModel):
    """取消關注的響應"""
    follower_id: int = Field(..., description="關注者ID")
    followee_id: int = Field(..., description="被關注者ID")
    follower_following_count: int = Field(..., description="關注者的關注數")
    followee_followers_count: int = Field(..., description="被關注者的粉絲數")


class ViewPostResponse(BaseModel):
    """檢視帖子的響應"""
    post_id: int = Field(..., description="帖子ID")
    author_id: int = Field(..., description="作者ID")
    content: str = Field(..., description="帖子內容")
    post_type: str = Field(..., description="帖子型別")
    likes_count: int = Field(..., description="點贊數")
    comments_count: int = Field(..., description="評論數")
    reposts_count: int = Field(..., description="轉發數")
    view_count: int = Field(..., description="瀏覽數")
    created_at: str = Field(..., description="建立時間")
    tags: List[str] = Field(default_factory=list, description="話題標籤列表")
    topic_category: Optional[str] = Field(None, description="主要話題分類")


class CommentOnPostResponse(BaseModel):
    """評論帖子的響應"""
    comment_id: int = Field(..., description="評論ID")
    post_id: int = Field(..., description="帖子ID")
    user_id: int = Field(..., description="評論者ID")
    content: str = Field(..., description="評論內容")
    total_comments: int = Field(..., description="帖子當前總評論數")


class RepostResponse(BaseModel):
    """轉發帖子的響應"""
    new_post_id: int = Field(..., description="新帖子ID")
    original_post_id: int = Field(..., description="原帖子ID")
    user_id: int = Field(..., description="轉發者ID")
    comment: str = Field("", description="轉發評論")
    original_reposts_count: int = Field(..., description="原帖當前轉發數")


class RefreshFeedResponse(BaseModel):
    """重新整理Feed的響應"""
    user_id: int = Field(..., description="使用者ID")
    algorithm: str = Field(..., description="推薦演算法")
    posts: List[dict] = Field(default_factory=list, description="推薦帖子列表")
    count: int = Field(..., description="返回的帖子數量")


class SearchPostsResponse(BaseModel):
    """搜尋帖子的響應"""
    keyword: str = Field(..., description="搜尋關鍵詞")
    tags: List[str] = Field(default_factory=list, description="標籤過濾")
    sort_by: str = Field(..., description="排序方式")
    posts: List[dict] = Field(default_factory=list, description="匹配的帖子")
    count: int = Field(..., description="返回的帖子數量")
    total_matched: int = Field(..., description="總匹配數")


class ObserveUserResponse(BaseModel):
    """使用者觀察響應 - 用於 <observe> 指令"""
    user_id: int = Field(..., description="使用者ID")
    username: str = Field(..., description="使用者名稱")
    followers_count: int = Field(0, description="粉絲數")
    following_count: int = Field(0, description="關注數")
    posts_count: int = Field(0, description="帖子數")
    profile: dict = Field(default_factory=dict, description="使用者檔案摘要")
    recent_interactions: List[dict] = Field(default_factory=list, description="最近收到的互動")
    recent_activity: List[dict] = Field(default_factory=list, description="最近自己的動態")
    social_updates: List[dict] = Field(default_factory=list, description="最近社交關係更新")
    recent_feed: List[dict] = Field(default_factory=list, description="最近的 Feed 帖子")
    available_actions: List[str] = Field(default_factory=list, description="可用的行為")
