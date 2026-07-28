import random
import string
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from google.oauth2 import id_token
from google.auth.transport import requests
from django.conf import settings

User = get_user_model()

class GoogleAuthView(APIView):
    permission_classes = []  # Този път трябва да е публично достъпен

    def post(self, request):
        token = request.data.get('credential')
        if not token:
            return Response(
                {'error': 'Липсва Google credential token.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Валидиране на токена през библиотеката на Google
            id_info = id_token.verify_oauth2_token(
                token, 
                requests.Request(), 
                settings.GOOGLE_CLIENT_ID
            )

            # Проверка на издателя на токена
            if id_info['iss'] not in ['accounts.google.com', 'https://accounts.google.com']:
                return Response(
                    {'error': 'Невалиден издател на токена.'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            email = id_info.get('email')
            first_name = id_info.get('given_name', '')
            last_name = id_info.get('family_name', '')

            if not email:
                return Response(
                    {'error': 'Имейл адресът не е предоставен от Google.'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Вземаме първата част от имейла
            base_name = email.split('@')[0]
            
            # Функция за добавяне на рандом низ (начало, среда или край)
            def generate_random_username(name):
                # Генерира 5 случайни символа (букви и цифри)
                random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
                
                # Избираме рандом позиция: 0 = начало, 1 = среда, 2 = край
                position = random.choice([0, 1, 2])
                
                if position == 0:
                    return f"{random_chars}{name}"
                elif position == 2:
                    return f"{name}{random_chars}"
                else:
                    mid = len(name) // 2
                    return f"{name[:mid]}{random_chars}{name[mid:]}"

            username = generate_random_username(base_name)
            
            # Предпазване от дублиране в базата (ако случайно се падне същия низ)
            while User.objects.filter(username=username).exclude(email=email).exists():
                username = generate_random_username(base_name)

            # Търсене или създаване на потребител с този имейл
            user, created = User.objects.get_or_create(
                email=email, 
                defaults={
                    'username': username,
                    'first_name': first_name,
                    'last_name': last_name,
                    'is_active': True
                }
            )

            # Генериране на Simple JWT токени (Access & Refresh) за сесията
            refresh = RefreshToken.for_user(user)
            
            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email
                }
            }, status=status.HTTP_200_OK)

        except ValueError:
            # Токенът е изтекъл, подправен или грешен
            return Response(
                {'error': 'Невалиден Google токен.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': f'Сървърна грешка: {str(e)}'}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )