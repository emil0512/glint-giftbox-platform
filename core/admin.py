from django.contrib import admin

from .models import (
    BoxConfiguration,
    BoxItem,
    BoxSelection,
    CatalogAssignment,
    Category,
    EventOccasion,
    GiftBox,
    Order,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'city')
    search_fields = ('user__username', 'user__email', 'phone', 'city')


@admin.register(EventOccasion)
class EventOccasionAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_editable = ('is_active',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active')
    list_editable = ('is_active',)
    search_fields = ('name',)


class CatalogAssignmentInline(admin.TabularInline):
    model = GiftBox.available_items.through
    fk_name = 'gift_box'
    extra = 1


@admin.register(GiftBox)
class GiftBoxAdmin(admin.ModelAdmin):
    list_display = ('name', 'event', 'base_price', 'stock', 'is_active')
    list_filter = ('event', 'is_active')
    search_fields = ('name',)
    list_editable = ('base_price', 'stock', 'is_active')
    inlines = (CatalogAssignmentInline,)


class BoxImageAndCategoryInline(admin.TabularInline):
    model = CatalogAssignment
    fk_name = 'box_item'
    extra = 1


@admin.register(BoxItem)
class BoxItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_name', 'price', 'stock', 'is_active')
    search_fields = ('name', 'catalog_assignments__category__name')
    list_editable = ('price', 'stock', 'is_active')
    inlines = (BoxImageAndCategoryInline,)


@admin.register(CatalogAssignment)
class CatalogAssignmentAdmin(admin.ModelAdmin):
    list_display = ('box_item', 'category', 'gift_box', 'image', 'is_default')
    list_filter = ('category', 'gift_box', 'is_default')


class BoxSelectionInline(admin.TabularInline):
    model = BoxSelection
    extra = 0


@admin.register(BoxConfiguration)
class BoxConfigurationAdmin(admin.ModelAdmin):
    list_display = ('id', 'box_name', 'status', 'user', 'quantity', 'unit_price')
    list_filter = ('status',)
    inlines = (BoxSelectionInline,)


class ConfigurationInline(admin.TabularInline):
    model = BoxConfiguration
    extra = 0
    readonly_fields = (
        'gift_box', 'box_name', 'base_price', 'unit_price_snapshot',
        'personalization_name', 'personalization_message', 'quantity',
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'full_name', 'grand_total', 'order_status', 'created_at')
    list_filter = ('order_status', 'payment_status', 'created_at')
    search_fields = (
        'order_number', 'contact_full_name', 'contact_email', 'contact_phone',
    )
    inlines = (ConfigurationInline,)
    list_editable = ('order_status',)
