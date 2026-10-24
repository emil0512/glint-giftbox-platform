from django.urls import path
from . import views

urlpatterns = [
    # Public & Catalog
    path('', views.home, name='home'),
    path('gift-boxes/', views.gift_boxes, name='gift_boxes'),
    path('gift-boxes/<int:event_id>/', views.gift_boxes_by_event, name='gift_boxes_by_event'),
    path('gift-box/<int:box_id>/', views.gift_box_detail, name='gift_box_detail'),
    path('search/', views.search_gift_boxes, name='search_gift_boxes'),

    # 3-Stage Customizer Builder & Drafts
    path('builder/<int:box_id>/', views.customizer_builder, name='customizer_builder'),
    path('builder/<int:box_id>/add-to-cart/', views.add_to_cart_from_builder, name='add_to_cart'),
    path('api/save-draft/', views.save_draft_api, name='save_draft_api'),
    path('drafts/delete/<int:draft_id>/', views.delete_draft, name='delete_draft'),
    path('drafts/rename/<int:draft_id>/', views.rename_draft, name='rename_draft'),

    # Shopping Cart
    path('cart/', views.cart_view, name='cart'),
    path('cart/update/<int:item_id>/<str:action>/', views.update_cart_quantity, name='update_cart_quantity'),
    path('cart/remove/<int:item_id>/', views.remove_from_cart, name='remove_from_cart'),

    # Checkout & Payment
    path('checkout/', views.checkout, name='checkout'),
    path('payment/', views.payment_gateway_view, name='payment'),
    path('order-confirmation/<str:order_number>/', views.order_confirmation_view, name='order_confirmation'),

    # Customer Profile & Orders
    path('profile/', views.profile, name='profile'),
    path('order/<str:order_number>/', views.order_detail_view, name='order_detail'),
    path('order/<str:order_number>/invoice.pdf', views.order_invoice_pdf, name='order_invoice_pdf'),
    path('order/<str:order_number>/cancel/', views.cancel_customer_order, name='cancel_order'),

    # Authentication
    path('register/', views.register, name='register'),
    path('activate/<uidb64>/<token>/', views.activate_account, name='activate_account'),
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('reset-password/', views.reset_password_otp, name='reset_password_otp'),
    path('terms/', views.terms_of_service, name='terms_of_service'),
    path('privacy/', views.privacy_policy, name='privacy_policy'),

    # Custom Admin Portal
    path('admin-portal/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-portal/products/', views.admin_products_list, name='admin_products'),
    path('admin-portal/products/new-box/', views.admin_box_create, name='admin_box_create'),
    path('admin-portal/products/box/<int:pk>/edit/', views.admin_box_edit, name='admin_box_edit'),
    path('admin-portal/products/box/<int:pk>/delete/', views.admin_box_delete, name='admin_box_delete'),
    path('admin-portal/products/new-item/', views.admin_box_item_create, name='admin_box_item_create'),
    path('admin-portal/products/item/<int:pk>/edit/', views.admin_box_item_edit, name='admin_box_item_edit'),
    path('admin-portal/products/item/<int:pk>/delete/', views.admin_box_item_delete, name='admin_box_item_delete'),
    path('admin-portal/orders/', views.admin_orders_list, name='admin_orders'),
    path('admin-portal/orders/<str:order_number>/', views.admin_order_detail, name='admin_order_detail'),
    path('admin-portal/orders/<str:order_number>/packing-slip/', views.admin_packing_slip, name='admin_packing_slip'),
    path('admin-portal/export-orders/', views.admin_export_orders_csv, name='admin_export_orders'),
]
